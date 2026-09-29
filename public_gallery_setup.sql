-- Apply once in the existing Supabase SQL Editor after multi_role_setup.sql.
-- Existing submissions, comments, profiles and meetings are retained.
begin;

alter table public.submissions
  add column if not exists presentation_category text not null default 'project_idea'
    check (presentation_category in ('project_idea','oral_presentation','poster')),
  add column if not exists support_request text;

create index if not exists submissions_public_category_idx
  on public.submissions(presentation_category, event_id)
  where status = 'approved';

drop policy if exists "Visitors view events" on public.platform_events;
create policy "Visitors view events" on public.platform_events
  for select to anon using (true);
grant select on public.platform_events to anon;

-- No guest email is exposed through table grants or the public RPC.
create table if not exists public.platform_guest_comments (
  id uuid primary key default gen_random_uuid(),
  submission_id uuid not null references public.submissions(id) on delete cascade,
  guest_name text not null,
  guest_email text not null,
  comment_text text not null,
  status text not null default 'approved'
    check (status in ('pending','approved','rejected')),
  created_at timestamptz not null default now()
);
create index if not exists guest_comments_review_idx
  on public.platform_guest_comments(submission_id,status,created_at);
alter table public.platform_guest_comments enable row level security;
revoke all on public.platform_guest_comments from public, anon, authenticated;

create table if not exists public.platform_author_replies (
  id uuid primary key default gen_random_uuid(),
  submission_id uuid not null references public.submissions(id) on delete cascade,
  parent_key text not null,
  author_id uuid not null references auth.users(id),
  comment_text text not null,
  created_at timestamptz not null default now()
);
alter table public.platform_author_replies enable row level security;
revoke all on public.platform_author_replies from public, anon, authenticated;

create or replace function public.platform_public_gallery()
returns table (
  id uuid, event_id uuid, owner_id uuid, title text, abstract_text text,
  short_summary text, thematic_area text, keywords text[], poster_url text,
  video_url text, presentation_category text, support_request text,
  author_name text, organisation text, created_at timestamptz
) language sql stable security definer set search_path = '' as $$
  select s.id, s.event_id, s.owner_id, s.title, s.abstract_text,
         s.short_summary, s.thematic_area, s.keywords, s.poster_url,
         s.video_url, s.presentation_category, s.support_request,
         p.full_name, p.organisation, s.created_at
  from public.submissions s left join public.profiles p on p.id = s.owner_id
  where s.status = 'approved'::public.submission_status
  order by s.created_at desc
  limit 500;
$$;

create or replace function public.platform_guest_comment(
  p_submission_id uuid, p_name text, p_email text, p_comment text
) returns void language plpgsql security definer set search_path = '' as $$
begin
  if not exists (select 1 from public.submissions
                 where id = p_submission_id and status = 'approved'::public.submission_status) then
    raise exception 'Submission unavailable';
  end if;
  if length(trim(coalesce(p_name,''))) not between 2 and 120 or
     length(trim(coalesce(p_email,''))) not between 5 and 254 or
     trim(p_email) !~* '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' or
     length(trim(coalesce(p_comment,''))) not between 2 and 2000 then
    raise exception 'Provide a name, valid email and comment (2–2000 characters)';
  end if;
  if (select count(*) from public.platform_guest_comments
      where guest_email = lower(trim(p_email))
        and created_at > now() - interval '1 hour') >= 5 then
    raise exception 'Please wait before posting more comments';
  end if;
  insert into public.platform_guest_comments
    (submission_id,guest_name,guest_email,comment_text,status)
  values (p_submission_id,trim(p_name),lower(trim(p_email)),trim(p_comment),'approved');
end;
$$;

create or replace function public.platform_public_comments(p_submission_id uuid)
returns table(comment_key text, display_name text, comment_text text,
              parent_key text, created_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not exists (select 1 from public.submissions
                 where id = p_submission_id and status = 'approved'::public.submission_status) then
    return;
  end if;
  return query
    select combined.comment_key, combined.display_name, combined.comment_text,
           combined.parent_key, combined.created_at
    from (
      select 'member:' || c.id::text as comment_key,
             coalesce(p.full_name,'Participant')::text as display_name,
             c.comment_text, null::text as parent_key, c.created_at
        from public.comments c left join public.profiles p on p.id = c.author_id
       where c.submission_id = p_submission_id and c.status = 'visible'::public.comment_status
      union all
      select 'guest:' || g.id::text, g.guest_name, g.comment_text,
             null::text, g.created_at
        from public.platform_guest_comments g
       where g.submission_id = p_submission_id and g.status = 'approved'
      union all
      select 'reply:' || r.id::text, coalesce(p.full_name,'Author') || ' (author)',
             r.comment_text, r.parent_key, r.created_at
        from public.platform_author_replies r
        left join public.profiles p on p.id = r.author_id
       where r.submission_id = p_submission_id
    ) combined order by combined.created_at;
end;
$$;

create or replace function public.platform_author_reply(
  p_submission_id uuid, p_parent_key text, p_reply text
) returns void language plpgsql security definer set search_path = '' as $$
begin
  if (select auth.uid()) is null or not exists (
    select 1 from public.submissions
    where id = p_submission_id and owner_id = (select auth.uid())
      and status = 'approved'::public.submission_status
  ) then
    raise exception 'Only the signed-in submission author can reply' using errcode = '42501';
  end if;
  if length(trim(coalesce(p_reply,''))) not between 2 and 2000 then
    raise exception 'Reply must have 2–2000 characters';
  end if;
  if p_parent_key !~ '^(member|guest):[0-9a-fA-F-]{36}$' then
    raise exception 'Comment unavailable';
  end if;
  if not (
    (p_parent_key like 'member:%' and exists (
      select 1 from public.comments
      where id = substring(p_parent_key from 8)::uuid
        and submission_id = p_submission_id and status = 'visible'::public.comment_status
    )) or
    (p_parent_key like 'guest:%' and exists (
      select 1 from public.platform_guest_comments
      where id = substring(p_parent_key from 7)::uuid
        and submission_id = p_submission_id and status = 'approved'
    ))
  ) then raise exception 'Comment unavailable'; end if;
  insert into public.platform_author_replies(submission_id,parent_key,author_id,comment_text)
  values (p_submission_id,p_parent_key,(select auth.uid()),trim(p_reply));
end;
$$;

create or replace function public.platform_guest_comment_queue(p_event_id uuid)
returns table(id uuid, submission_title text, guest_name text, comment_text text)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not public.platform_is_event_admin(p_event_id) then
    raise exception 'Event admin access required' using errcode = '42501';
  end if;
  return query select g.id,s.title,g.guest_name,g.comment_text
    from public.platform_guest_comments g
    join public.submissions s on s.id = g.submission_id
    where s.event_id = p_event_id and g.status = 'pending'
    order by g.created_at;
end;
$$;

create or replace function public.platform_review_guest_comment(
  p_comment_id uuid, p_decision text
) returns void language plpgsql security definer set search_path = '' as $$
declare v_event_id uuid;
begin
  select s.event_id into v_event_id
    from public.platform_guest_comments g
    join public.submissions s on s.id = g.submission_id
    where g.id = p_comment_id and g.status = 'pending' for update of g;
  if v_event_id is null or not public.platform_is_event_admin(v_event_id) then
    raise exception 'Comment unavailable or event admin access required' using errcode = '42501';
  end if;
  if p_decision not in ('approved','rejected') then
    raise exception 'Invalid decision';
  end if;
  update public.platform_guest_comments set status = p_decision where id = p_comment_id;
end;
$$;

revoke all on function public.platform_public_gallery() from public;
revoke all on function public.platform_public_comments(uuid) from public;
revoke all on function public.platform_guest_comment(uuid,text,text,text) from public;
revoke all on function public.platform_author_reply(uuid,text,text) from public,anon;
revoke all on function public.platform_guest_comment_queue(uuid) from public,anon;
revoke all on function public.platform_review_guest_comment(uuid,text) from public,anon;
grant execute on function public.platform_public_gallery() to anon,authenticated;
grant execute on function public.platform_public_comments(uuid) to anon,authenticated;
grant execute on function public.platform_guest_comment(uuid,text,text,text) to anon;
grant execute on function public.platform_author_reply(uuid,text,text) to authenticated;
grant execute on function public.platform_guest_comment_queue(uuid) to authenticated;
grant execute on function public.platform_review_guest_comment(uuid,text) to authenticated;

commit;

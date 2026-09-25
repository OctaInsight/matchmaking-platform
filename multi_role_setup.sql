-- Run once in Supabase SQL Editor. Keeps existing profiles, submissions, comments and meetings.
-- The verified account octainsight@gmail.com is the only super admin.
-- Event sub-admins are assigned by that account from the app after they register.

create table if not exists public.platform_events (
  id uuid primary key default gen_random_uuid(),
  title text not null check (length(trim(title)) between 5 and 200),
  description text not null default '',
  starts_at timestamptz,
  ends_at timestamptz,
  is_active boolean not null default true,
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  check (starts_at is null or ends_at is null or ends_at > starts_at)
);

create table if not exists public.platform_event_admins (
  event_id uuid not null references public.platform_events(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  granted_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  primary key (event_id, user_id)
);

create table if not exists public.platform_user_types (
  user_id uuid primary key references auth.users(id) on delete cascade,
  user_type text not null check (user_type in ('project_owner', 'investor', 'audience')),
  updated_at timestamptz not null default now()
);

alter table public.submissions
  add column if not exists event_id uuid references public.platform_events(id);
create index if not exists submissions_event_status_idx
  on public.submissions(event_id, status);

alter table public.platform_events enable row level security;
alter table public.platform_event_admins enable row level security;
alter table public.platform_user_types enable row level security;

drop policy if exists "Participants view events" on public.platform_events;
create policy "Participants view events" on public.platform_events
  for select to authenticated using (true);

drop policy if exists "Users read own participant type" on public.platform_user_types;
create policy "Users read own participant type" on public.platform_user_types
  for select to authenticated using (user_id = (select auth.uid()));
drop policy if exists "Users choose participant type" on public.platform_user_types;
create policy "Users choose participant type" on public.platform_user_types
  for insert to authenticated with check (user_id = (select auth.uid()));
drop policy if exists "Users change participant type" on public.platform_user_types;
create policy "Users change participant type" on public.platform_user_types
  for update to authenticated using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

grant select on public.platform_events to authenticated;
grant select, insert, update on public.platform_user_types to authenticated;
revoke all on public.platform_event_admins from anon, authenticated;

create or replace function public.platform_is_super_admin()
returns boolean language sql stable security definer set search_path = '' as $$
  select (select auth.uid()) is not null and exists (
    select 1 from auth.users u
    where u.id = (select auth.uid())
      and lower(u.email) = 'octainsight@gmail.com'
      and u.email_confirmed_at is not null
  );
$$;

create or replace function public.platform_is_event_admin(p_event_id uuid)
returns boolean language sql stable security definer set search_path = '' as $$
  select public.platform_is_super_admin() or exists (
    select 1 from public.platform_event_admins ea
    where ea.event_id = p_event_id and ea.user_id = (select auth.uid())
  );
$$;

create or replace function public.platform_create_event(
  p_title text, p_description text, p_starts_at timestamptz, p_ends_at timestamptz
) returns uuid language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  if not public.platform_is_super_admin() then
    raise exception 'Super admin access required' using errcode = '42501';
  end if;
  if length(trim(coalesce(p_title,''))) < 5 then
    raise exception 'Event title must have at least 5 characters';
  end if;
  if p_starts_at is not null and p_ends_at is not null and p_ends_at <= p_starts_at then
    raise exception 'Event end must be after start';
  end if;
  insert into public.platform_events(title,description,starts_at,ends_at,created_by)
  values (trim(p_title), coalesce(trim(p_description),''), p_starts_at,p_ends_at,(select auth.uid()))
  returning id into v_id;
  return v_id;
end;
$$;

create or replace function public.platform_grant_event_admin(
  p_event_id uuid, p_email text
) returns void language plpgsql security definer set search_path = '' as $$
declare v_user uuid;
begin
  if not public.platform_is_super_admin() then
    raise exception 'Super admin access required' using errcode = '42501';
  end if;
  if not exists(select 1 from public.platform_events where id = p_event_id) then
    raise exception 'Event not found';
  end if;
  select id into v_user from auth.users
    where lower(email) = lower(trim(p_email)) and email_confirmed_at is not null;
  if v_user is null then
    raise exception 'That email has no confirmed account. Ask the person to register first.';
  end if;
  insert into public.platform_event_admins(event_id,user_id,granted_by)
  values (p_event_id,v_user,(select auth.uid()))
  on conflict (event_id,user_id) do nothing;
end;
$$;

create or replace function public.platform_revoke_event_admin(
  p_event_id uuid, p_user_id uuid
) returns void language plpgsql security definer set search_path = '' as $$
begin
  if not public.platform_is_super_admin() then
    raise exception 'Super admin access required' using errcode = '42501';
  end if;
  delete from public.platform_event_admins
  where event_id = p_event_id and user_id = p_user_id;
end;
$$;

create or replace function public.platform_list_event_admins(p_event_id uuid)
returns table(user_id uuid, email text, full_name text)
language plpgsql security definer set search_path = '' as $$
begin
  if not public.platform_is_super_admin() then
    raise exception 'Super admin access required' using errcode = '42501';
  end if;
  return query select ea.user_id, u.email::text, p.full_name
    from public.platform_event_admins ea
    join auth.users u on u.id = ea.user_id
    left join public.profiles p on p.id = ea.user_id
    where ea.event_id = p_event_id order by u.email;
end;
$$;

-- A signed-in author cannot approve their own abstract or move it between events.
create or replace function public.platform_guard_submission_update()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select auth.uid()) is not null and
     current_setting('app.platform_review', true) is distinct from 'true' then
    if new.owner_id is distinct from old.owner_id or
       new.event_id is distinct from old.event_id or
       new.status in ('approved','rejected') or
       new.approved_by is distinct from old.approved_by or
       new.approved_at is distinct from old.approved_at then
      raise exception 'Review and event assignment require an event admin';
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists protect_submission_review on public.submissions;
drop trigger if exists platform_guard_submission_update on public.submissions;
create trigger platform_guard_submission_update before update on public.submissions
for each row execute function public.platform_guard_submission_update();

create or replace function public.platform_guard_submission_insert()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select auth.uid()) is not null then
    if new.owner_id is distinct from (select auth.uid()) or
       new.event_id is null or new.status not in ('draft','submitted') or
       not exists(select 1 from public.platform_user_types t
         where t.user_id = (select auth.uid()) and t.user_type = 'project_owner') then
      raise exception 'Choose Project owner and an event before submitting';
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists platform_guard_submission_insert on public.submissions;
create trigger platform_guard_submission_insert before insert on public.submissions
for each row execute function public.platform_guard_submission_insert();

create or replace function public.platform_review_queue(p_event_id uuid)
returns setof public.submissions
language plpgsql security definer set search_path = '' as $$
begin
  if not public.platform_is_event_admin(p_event_id) then
    raise exception 'Event admin access required' using errcode = '42501';
  end if;
  return query select s.* from public.submissions s
    where s.event_id = p_event_id and s.status::text = 'submitted'
    order by s.submitted_at nulls last, s.created_at;
end;
$$;

create or replace function public.platform_review_submission(
  p_submission_id uuid, p_decision text
) returns void language plpgsql security definer set search_path = '' as $$
declare v_event_id uuid;
begin
  select event_id into v_event_id from public.submissions
    where id = p_submission_id and status::text = 'submitted' for update;
  if v_event_id is null or not public.platform_is_event_admin(v_event_id) then
    raise exception 'Submission unavailable or event admin access required' using errcode = '42501';
  end if;
  if p_decision not in ('approved','rejected') then
    raise exception 'Decision must be approved or rejected';
  end if;
  perform set_config('app.platform_review','true',true);
  update public.submissions
  set status = p_decision::public.submission_status,
      approved_at = case when p_decision = 'approved' then now() else null end,
      approved_by = case when p_decision = 'approved' then (select auth.uid()) else null end
  where id = p_submission_id and status::text = 'submitted';
end;
$$;

create or replace function public.platform_unassigned_submissions()
returns setof public.submissions
language plpgsql security definer set search_path = '' as $$
begin
  if not public.platform_is_super_admin() then
    raise exception 'Super admin access required' using errcode = '42501';
  end if;
  return query select s.* from public.submissions s
    where s.event_id is null and s.status::text = 'submitted'
    order by s.created_at;
end;
$$;

create or replace function public.platform_assign_submission(
  p_submission_id uuid, p_event_id uuid
) returns void language plpgsql security definer set search_path = '' as $$
begin
  if not public.platform_is_super_admin() then
    raise exception 'Super admin access required' using errcode = '42501';
  end if;
  if not exists (select 1 from public.platform_events where id = p_event_id) then
    raise exception 'Event not found';
  end if;
  perform set_config('app.platform_review','true',true);
  update public.submissions set event_id = p_event_id
    where id = p_submission_id and event_id is null and status::text = 'submitted';
  if not found then
    raise exception 'Unassigned submitted abstract not found';
  end if;
end;
$$;

-- Retire the previous global-admin review RPCs if that setup was already run.
drop function if exists public.admin_pending_submissions();
drop function if exists public.admin_review_submission(uuid,text);

revoke all on function public.platform_is_super_admin() from public, anon;
revoke all on function public.platform_is_event_admin(uuid) from public, anon;
revoke all on function public.platform_create_event(text,text,timestamptz,timestamptz) from public, anon;
revoke all on function public.platform_grant_event_admin(uuid,text) from public, anon;
revoke all on function public.platform_revoke_event_admin(uuid,uuid) from public, anon;
revoke all on function public.platform_list_event_admins(uuid) from public, anon;
revoke all on function public.platform_review_queue(uuid) from public, anon;
revoke all on function public.platform_review_submission(uuid,text) from public, anon;
grant execute on function public.platform_is_super_admin() to authenticated;
grant execute on function public.platform_is_event_admin(uuid) to authenticated;
grant execute on function public.platform_create_event(text,text,timestamptz,timestamptz) to authenticated;
grant execute on function public.platform_grant_event_admin(uuid,text) to authenticated;
grant execute on function public.platform_revoke_event_admin(uuid,uuid) to authenticated;
grant execute on function public.platform_list_event_admins(uuid) to authenticated;
grant execute on function public.platform_review_queue(uuid) to authenticated;
grant execute on function public.platform_review_submission(uuid,text) to authenticated;

revoke all on function public.platform_unassigned_submissions() from public, anon;
revoke all on function public.platform_assign_submission(uuid,uuid) from public, anon;
grant execute on function public.platform_unassigned_submissions() to authenticated;
grant execute on function public.platform_assign_submission(uuid,uuid) to authenticated;

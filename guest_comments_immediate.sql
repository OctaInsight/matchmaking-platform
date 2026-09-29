-- Apply in the existing Supabase SQL Editor after public_gallery_setup.sql.
-- New guest comments are visible immediately. Earlier pending comments also become visible.
begin;

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
    (submission_id, guest_name, guest_email, comment_text, status)
  values (p_submission_id, trim(p_name), lower(trim(p_email)), trim(p_comment), 'approved');
end;
$$;

update public.platform_guest_comments
set status = 'approved'
where status = 'pending';

revoke all on function public.platform_guest_comment(uuid,text,text,text) from public;
grant execute on function public.platform_guest_comment(uuid,text,text,text) to anon;

commit;

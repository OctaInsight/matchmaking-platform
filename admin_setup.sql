-- Run in the Supabase SQL Editor after reviewing the role assignment below.
-- Uses the EXISTING profiles and submissions tables; does not recreate them.

-- Prevent a signed-in user from elevating their own profile role via the Data API.
create or replace function public.protect_profile_role()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select auth.uid()) is not null and new.role is distinct from old.role then
    raise exception 'Role changes require database administration';
  end if;
  return new;
end;
$$;
drop trigger if exists protect_profile_role on public.profiles;
create trigger protect_profile_role before update on public.profiles
for each row execute function public.protect_profile_role();

-- A submission owner may edit content, but cannot approve or reject their own work.
-- Review decisions are made by the guarded review_submission function below.
create or replace function public.protect_submission_review()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select auth.uid()) is not null and
     current_setting('app.review_authorized', true) is distinct from 'true' and
     (new.status in ('approved', 'rejected') or
      new.approved_by is distinct from old.approved_by or
      new.approved_at is distinct from old.approved_at) then
    raise exception 'Review decisions require an admin';
  end if;
  return new;
end;
$$;
drop trigger if exists protect_submission_review on public.submissions;
create trigger protect_submission_review before update on public.submissions
for each row execute function public.protect_submission_review();

create or replace function public.admin_pending_submissions()
returns setof public.submissions
language plpgsql security definer set search_path = '' as $$
begin
  if (select auth.uid()) is null or not exists (
    select 1 from public.profiles p
    where p.id = (select auth.uid()) and p.role::text = 'admin'
  ) then
    raise exception 'Admin access required' using errcode = '42501';
  end if;
  return query
    select s.* from public.submissions s
    where s.status::text = 'submitted'
    order by s.submitted_at nulls last, s.created_at;
end;
$$;

create or replace function public.admin_review_submission(
  p_submission_id uuid, p_decision text
)
returns void
language plpgsql security definer set search_path = '' as $$
begin
  if (select auth.uid()) is null or not exists (
    select 1 from public.profiles p
    where p.id = (select auth.uid()) and p.role::text = 'admin'
  ) then
    raise exception 'Admin access required' using errcode = '42501';
  end if;
  if p_decision not in ('approved', 'rejected') then
    raise exception 'Decision must be approved or rejected';
  end if;
  perform set_config('app.review_authorized', 'true', true);
  update public.submissions
  set status = p_decision::public.submission_status,
      approved_at = case when p_decision = 'approved' then now() else null end,
      approved_by = case when p_decision = 'approved' then (select auth.uid()) else null end
  where id = p_submission_id and status::text = 'submitted';
  if not found then
    raise exception 'Submission is no longer awaiting review';
  end if;
end;
$$;

revoke all on function public.admin_pending_submissions() from public, anon;
revoke all on function public.admin_review_submission(uuid, text) from public, anon;
grant execute on function public.admin_pending_submissions() to authenticated;
grant execute on function public.admin_review_submission(uuid, text) to authenticated;

-- Assign one trusted account after confirming the user_role enum contains 'admin'.
-- Replace the email and run this statement separately in SQL Editor:
--
-- update public.profiles p
-- set role = 'admin'::public.user_role
-- from auth.users u
-- where p.id = u.id and u.email = 'YOUR_EMAIL@example.com';

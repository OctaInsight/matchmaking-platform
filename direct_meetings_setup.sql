-- Run once in Supabase SQL Editor to allow meetings between registered users.
-- Existing abstract-linked meeting requests remain unchanged.
alter table public.meeting_requests
  alter column submission_id drop not null;

drop policy if exists "Direct meetings between users" on public.meeting_requests;
create policy "Direct meetings between users"
on public.meeting_requests for insert to authenticated
with check (
  requester_id = (select auth.uid())
  and recipient_id <> requester_id
  and submission_id is null
  and proposed_end > proposed_start
  and proposed_start > now()
  and exists (
    select 1 from public.profiles p where p.id = recipient_id
  )
);

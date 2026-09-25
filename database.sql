-- Run once in the Supabase SQL Editor.
create extension if not exists pgcrypto;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text not null check (length(trim(full_name)) between 2 and 120),
  organisation text not null default '',
  bio text not null default '',
  created_at timestamptz not null default now()
);

create table if not exists public.abstracts (
  id uuid primary key default gen_random_uuid(),
  author_id uuid not null references public.profiles(id) on delete cascade,
  title text not null check (length(trim(title)) between 5 and 200),
  category text not null check (category in ('Project Brokerage', 'Scale2Connect Matchmaking', 'Other')),
  abstract_text text not null check (length(trim(abstract_text)) between 30 and 5000),
  keywords text not null default '',
  poster_url text,
  video_url text,
  status text not null default 'published' check (status in ('draft', 'published')),
  created_at timestamptz not null default now()
);
create index if not exists abstracts_author_idx on public.abstracts(author_id);

create table if not exists public.meeting_requests (
  id uuid primary key default gen_random_uuid(),
  abstract_id uuid not null references public.abstracts(id) on delete cascade,
  requester_id uuid not null references public.profiles(id) on delete cascade,
  author_id uuid not null references public.profiles(id) on delete cascade,
  message text not null check (length(trim(message)) between 5 and 2000),
  proposed_at timestamptz not null,
  status text not null default 'pending' check (status in ('pending', 'accepted', 'declined', 'cancelled')),
  meeting_link text,
  created_at timestamptz not null default now(),
  check (requester_id <> author_id)
);
create index if not exists meeting_requester_idx on public.meeting_requests(requester_id);
create index if not exists meeting_author_idx on public.meeting_requests(author_id);

create table if not exists public.feedback (
  id uuid primary key default gen_random_uuid(),
  abstract_id uuid not null references public.abstracts(id) on delete cascade,
  sender_id uuid not null references public.profiles(id) on delete cascade,
  comment text not null check (length(trim(comment)) between 2 and 2000),
  created_at timestamptz not null default now()
);
create index if not exists feedback_abstract_idx on public.feedback(abstract_id);

alter table public.profiles enable row level security;
alter table public.abstracts enable row level security;
alter table public.meeting_requests enable row level security;
alter table public.feedback enable row level security;

create policy "Profiles readable by participants" on public.profiles
  for select to authenticated using (true);
create policy "Create own profile" on public.profiles
  for insert to authenticated with check (id = (select auth.uid()));
create policy "Update own profile" on public.profiles
  for update to authenticated using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

create policy "Published or owned abstracts readable" on public.abstracts
  for select to authenticated using (status = 'published' or author_id = (select auth.uid()));
create policy "Create own abstract" on public.abstracts
  for insert to authenticated with check (author_id = (select auth.uid()));
create policy "Update own abstract" on public.abstracts
  for update to authenticated using (author_id = (select auth.uid()))
  with check (author_id = (select auth.uid()));
create policy "Delete own abstract" on public.abstracts
  for delete to authenticated using (author_id = (select auth.uid()));

create policy "Participants read their requests" on public.meeting_requests
  for select to authenticated using (requester_id = (select auth.uid()) or author_id = (select auth.uid()));
create policy "Request a published abstract" on public.meeting_requests
  for insert to authenticated with check (
    requester_id = (select auth.uid()) and requester_id <> author_id
    and exists (select 1 from public.abstracts a
      where a.id = abstract_id and a.author_id = meeting_requests.author_id and a.status = 'published')
  );
create policy "Author responds to meeting" on public.meeting_requests
  for update to authenticated using (author_id = (select auth.uid()))
  with check (author_id = (select auth.uid()) and status in ('accepted', 'declined', 'pending'));
create policy "Requester cancels meeting" on public.meeting_requests
  for update to authenticated using (requester_id = (select auth.uid()))
  with check (requester_id = (select auth.uid()) and status = 'cancelled');

create policy "Feedback readable by participants" on public.feedback
  for select to authenticated using (true);
create policy "Create own feedback on published abstract" on public.feedback
  for insert to authenticated with check (
    sender_id = (select auth.uid()) and exists
      (select 1 from public.abstracts a where a.id = abstract_id and a.status = 'published')
  );
create policy "Delete own feedback" on public.feedback
  for delete to authenticated using (sender_id = (select auth.uid()));

-- Do not use the service_role key in Streamlit. Configure email confirmation in
-- Supabase Authentication and set your deployment URL as the redirect URL.

-- Keep meeting ownership and proposed time immutable. Only the requester may
-- cancel a pending request; only the author may accept or decline it.
create or replace function public.guard_meeting_update()
returns trigger language plpgsql set search_path = '' as $$
begin
  if new.id is distinct from old.id
     or new.abstract_id is distinct from old.abstract_id
     or new.requester_id is distinct from old.requester_id
     or new.author_id is distinct from old.author_id
     or new.message is distinct from old.message
     or new.proposed_at is distinct from old.proposed_at
     or new.created_at is distinct from old.created_at then
    raise exception 'Meeting identity and proposal cannot be changed';
  end if;
  if old.status <> 'pending' then
    raise exception 'A completed request cannot be changed';
  end if;
  if (select auth.uid()) = old.requester_id then
    if new.status <> 'cancelled' or new.meeting_link is distinct from old.meeting_link then
      raise exception 'Requester may only cancel a pending request';
    end if;
  elsif (select auth.uid()) = old.author_id then
    if new.status not in ('accepted', 'declined') then
      raise exception 'Author may only accept or decline a pending request';
    end if;
    if new.status = 'declined' and new.meeting_link is not null then
      raise exception 'Declined requests cannot have a meeting link';
    end if;
  else
    raise exception 'Not a meeting participant';
  end if;
  return new;
end;
$$;
drop trigger if exists guard_meeting_update on public.meeting_requests;
create trigger guard_meeting_update before update on public.meeting_requests
for each row execute function public.guard_meeting_update();

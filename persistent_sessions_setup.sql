-- Private server-side sessions. No access for visitors or signed-in browser clients.
begin;
create table if not exists public.platform_login_sessions (
  session_digest text primary key check (length(session_digest)=64),
  user_id uuid not null references auth.users(id) on delete cascade,
  encrypted_tokens text not null,
  last_activity_at timestamptz not null default now(),
  expires_at timestamptz not null
);
create index if not exists platform_login_sessions_expiry_idx on public.platform_login_sessions(expires_at);
alter table public.platform_login_sessions enable row level security;
revoke all on public.platform_login_sessions from public, anon, authenticated;
grant select, insert, update, delete on public.platform_login_sessions to service_role;
commit;

-- Run in the existing Supabase SQL Editor. Installs guarded admin operations.
begin;
create or replace function public.platform_cleanup_submission(p_id uuid)
returns void language plpgsql security definer set search_path = '' as $$
begin
  delete from public.meeting_requests where submission_id = p_id;
  if exists(select 1 from information_schema.columns where table_schema='public' and table_name='availability_slots' and column_name='submission_id') then
    execute 'delete from public.availability_slots where submission_id=$1' using p_id;
  end if;
  delete from public.platform_author_replies where submission_id = p_id;
  delete from public.platform_guest_comments where submission_id = p_id;
  delete from public.comments where submission_id = p_id;
  delete from public.submissions where id = p_id;
end;
$$;
revoke all on function public.platform_cleanup_submission(uuid) from public, anon, authenticated;

create or replace function public.platform_admin_submissions(p_event_id uuid)
returns setof public.submissions language plpgsql security definer set search_path = '' as $$
begin
  if not public.platform_is_event_admin(p_event_id) then raise exception 'Event admin access required' using errcode='42501'; end if;
  return query select s.* from public.submissions s where s.event_id=p_event_id order by s.created_at desc;
end;
$$;

create or replace function public.platform_remove_submission_content(p_submission_id uuid, p_action text)
returns void language plpgsql security definer set search_path = '' as $$
declare v_event uuid;
begin
  select event_id into v_event from public.submissions where id=p_submission_id for update;
  if not found then raise exception 'Submission not found'; end if;
  if not public.platform_is_super_admin() and not public.platform_is_event_admin(v_event) then raise exception 'Event admin access required' using errcode='42501'; end if;
  perform set_config('app.platform_review','true',true);
  if p_action='submission' then perform public.platform_cleanup_submission(p_submission_id);
  elsif p_action='poster' then update public.submissions set poster_url=null where id=p_submission_id;
  elsif p_action='video' then update public.submissions set video_url=null where id=p_submission_id;
  else raise exception 'Invalid removal action'; end if;
end;
$$;

create or replace function public.platform_remove_event(p_event_id uuid)
returns void language plpgsql security definer set search_path = '' as $$
declare item record;
begin
  if not public.platform_is_super_admin() then raise exception 'Super admin access required' using errcode='42501'; end if;
  perform 1 from public.platform_events where id=p_event_id for update;
  if not found then raise exception 'Event not found'; end if;
  for item in select id from public.submissions where event_id=p_event_id for update loop
    perform public.platform_cleanup_submission(item.id);
  end loop;
  delete from public.platform_event_admins where event_id=p_event_id;
  delete from public.platform_events where id=p_event_id;
end;
$$;

create or replace function public.platform_admin_users()
returns table(user_id uuid,email text,full_name text) language plpgsql security definer set search_path = '' as $$
begin
  if not public.platform_is_super_admin() then raise exception 'Super admin access required' using errcode='42501'; end if;
  return query select u.id,u.email::text,p.full_name::text from auth.users u left join public.profiles p on p.id=u.id
    where u.id<>(select auth.uid()) and lower(coalesce(u.email,''))<>'octainsight@gmail.com' order by u.email;
end;
$$;

create or replace function public.platform_remove_user(p_user_id uuid)
returns void language plpgsql security definer set search_path = '' as $$
declare v_email text; item record; col text;
begin
  if not public.platform_is_super_admin() then raise exception 'Super admin access required' using errcode='42501'; end if;
  select email into v_email from auth.users where id=p_user_id for update;
  if not found then raise exception 'User not found'; end if;
  if p_user_id=(select auth.uid()) or lower(coalesce(v_email,''))='octainsight@gmail.com' then raise exception 'The super admin account cannot be removed'; end if;
  perform set_config('app.platform_review','true',true);
  for item in select id from public.submissions where owner_id=p_user_id for update loop
    perform public.platform_cleanup_submission(item.id);
  end loop;
  delete from public.meeting_requests where requester_id=p_user_id or recipient_id=p_user_id;
  for col in select column_name from information_schema.columns where table_schema='public' and table_name='availability_slots' and column_name in ('owner_id','user_id') loop
    execute format('delete from public.availability_slots where %I=$1',col) using p_user_id;
  end loop;
  delete from public.platform_author_replies where author_id=p_user_id;
  delete from public.comments where author_id=p_user_id;
  update public.submissions set approved_by=null where approved_by=p_user_id;
  delete from public.platform_event_admins where user_id=p_user_id;
  update public.platform_event_admins set granted_by=(select auth.uid()) where granted_by=p_user_id;
  update public.platform_events set created_by=(select auth.uid()) where created_by=p_user_id;
  delete from public.platform_user_types where user_id=p_user_id;
  delete from public.profiles where id=p_user_id;
  delete from auth.users where id=p_user_id;
end;
$$;

revoke all on function public.platform_admin_submissions(uuid),public.platform_remove_submission_content(uuid,text),public.platform_remove_event(uuid),public.platform_admin_users(),public.platform_remove_user(uuid) from public, anon;
grant execute on function public.platform_admin_submissions(uuid),public.platform_remove_submission_content(uuid,text),public.platform_remove_event(uuid),public.platform_admin_users(),public.platform_remove_user(uuid) to authenticated;
commit;


create or replace function londiste.version()
returns text as $$
begin
    return '2.1.14';
end;
$$ language plpgsql;


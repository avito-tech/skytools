\set ECHO none
set log_error_verbosity = 'terse';
set client_min_messages = 'warning';
\set VERBOSITY 'terse'
\i ../pgq/pgq.sql
\i ../logtriga/logtriga.sql
\i ../londiste/londiste.sql
\i londiste_undo.sql
\set ECHO all


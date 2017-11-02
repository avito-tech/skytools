#! /usr/bin/env python

"""Londiste setup and sanity checker.

"""
import sys, os, time, skytools
from installer import *

# support set() on 2.3
if 'set' not in __builtins__:
    from sets import Set as set

__all__ = ['ProviderSetup', 'SubscriberSetup']

def normalize_table_name(curs, table):
    q_tbl_name = """select n.nspname, c.relname from pg_class c
                    join pg_namespace n on c.relnamespace = n.oid
                    where c.oid = %s::regclass"""
    curs.execute(q_tbl_name, [table])
    return curs.fetchone()

def find_column_types(curs, table):
    table_oid = skytools.get_table_oid(curs, table)
    if table_oid == None:
        return None

    key_sql = """
        SELECT k.attname FROM pg_index i, pg_attribute k
         WHERE i.indrelid = %d AND k.attrelid = i.indexrelid
           AND i.indisprimary AND k.attnum > 0 AND NOT k.attisdropped
        """ % table_oid

    # find columns
    q = """
        SELECT a.attname as name,
               CASE WHEN k.attname IS NOT NULL
                    THEN 'k' ELSE 'v' END AS type,
               format_type(a.atttypid, a.atttypmod) as rtype
         FROM pg_attribute a
            INNER JOIN pg_type t ON (t.oid = a.atttypid)
            LEFT JOIN (%s) k ON (k.attname = a.attname)
         WHERE a.attrelid = %d AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY a.attnum
         """ % (key_sql, table_oid)
    curs.execute(q)
    rows = curs.dictfetchall()
    return rows

def make_type_string(col_rows):
    res = map(lambda x: x['type'], col_rows)
    return "".join(res)

def convertGlobs(s):
    return s.replace('?', '.').replace('*', '.*')

def glob2regex(gpat):
    plist = [convertGlobs(s) for s in gpat.split('.')]
    return '^%s$' % '[.]'.join(plist)

class CommonSetup(skytools.DBScript):
    def __init__(self, args):
        skytools.DBScript.__init__(self, 'londiste', args)
        self.set_single_loop(1)
        self.pidfile = self.pidfile + ".setup"

        self.pgq_queue_name = self.cf.get("pgq_queue_name")
        self.consumer_id = self.cf.get("pgq_consumer_id", self.job_name)
        self.fake = self.cf.getint('fake', 0)
        self.lock_timeout = self.cf.getfloat('lock_timeout', 10)

        if len(self.args) < 3:
            self.log.error("need subcommand")
            sys.exit(1)

    def set_lock_timeout(self, curs):
        ms = int(1000 * self.lock_timeout)
        if ms > 0:
            q = "SET LOCAL statement_timeout = %d" % ms
            self.log.debug(q)
            curs.execute(q)

    def run(self):
        self.admin()

    def fetch_provider_table_list(self, curs, pattern='*'):
        q = """select table_name, trigger_name
                 from londiste.provider_get_table_list(%s)
                 where table_name ~ %s"""
        curs.execute(q, [self.pgq_queue_name, glob2regex(pattern)])
        return curs.dictfetchall()

    def get_provider_table_list(self, pattern='*'):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        list = self.fetch_provider_table_list(src_curs, pattern)
        src_db.commit()
        res = []
        for row in list:
            res.append(row['table_name'])
        return res

    def get_provider_seqs(self):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        q = """SELECT * from londiste.provider_get_seq_list(%s)"""
        src_curs.execute(q, [self.pgq_queue_name])
        src_db.commit()
        res = []
        for row in src_curs.fetchall():
            res.append(row[0])
        return res

    def get_all_seqs(self, curs):
        q = """SELECT n.nspname || '.' || c.relname
                 from pg_class c, pg_namespace n
                where n.oid = c.relnamespace 
                  and c.relkind = 'S'
                  and n.nspname not in ('pgq', 'londiste', 'pgq_node')
                  and n.nspname !~ '^pg_temp_.*'
                order by 1"""
        curs.execute(q)
        res = []
        for row in curs.fetchall():
            res.append(row[0])
        return res

    def check_provider_queue(self):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        q = "select count(1) from pgq.get_queue_info(%s)"
        src_curs.execute(q, [self.pgq_queue_name])
        ok = src_curs.fetchone()[0]
        src_db.commit()
        
        if not ok:
            self.log.error('Event queue does not exist yet')
            sys.exit(1)

    def get_provider_trigger(self, table):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        q = """ select
                    p.queue_name as queue_name,
                    p.table_name as table_name,
                    p.trigger_name as trigger_name,
                    string_to_array(encode(t.tgargs, 'escape'), E'\\\\000') as tgargs
                from londiste.provider_table p
                    left join pg_trigger t
                        on (t.tgrelid = londiste.find_table_oid(p.table_name) and t.tgname::text = p.trigger_name)
                where p.table_name = %s and p.queue_name = %s"""
        src_curs.execute(q, [table, self.pgq_queue_name])

        src_row = src_curs.fetchone()
        src_db.commit()

        return src_row

    def fetch_subscriber_tables(self, curs, pattern = '*'):
        q = "select * from londiste.subscriber_get_table_list(%s) where table_name ~ %s"
        curs.execute(q, [self.pgq_queue_name, glob2regex(pattern)])
        return curs.dictfetchall()

    def get_subscriber_table_list(self, pattern = '*'):
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        list = self.fetch_subscriber_tables(dst_curs, pattern)
        dst_db.commit()
        res = []
        for row in list:
            res.append(row['table_name'])
        return res

    def get_subscriber_child_tables_list(self, parents):
        q_func_name = "select londiste_undo.subscriber_child_tables_func(%s, %s)"
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        # TODO: read func_name from ini file?
        dst_curs.execute(q_func_name, ['public', 'get_childs_for_undo'])
        func_name = dst_curs.fetchone()[0]
        res = []
        if func_name:
            for tbl in parents:
                dst_curs.execute('select * from %s(%%s)' % func_name, [tbl])
                for row in dst_curs.fetchall():
                    res.append(row[0])
        dst_db.commit()
        return res

    def init_optparse(self, parser=None):
        p = skytools.DBScript.init_optparse(self, parser)
        p.add_option("--expect-sync", action="store_true", dest="expect_sync",
                    help = "no copy needed", default=False)
        p.add_option("--skip-truncate", action="store_true", dest="skip_truncate",
                    help = "dont delete old data", default=False)
        p.add_option("--force", action="store_true",
                    help="force", default=False)
        p.add_option("--all", action="store_true",
                    help="include all tables", default=False)
        p.add_option("--dry-run", action="store_true", dest="dry_run",
                help = "do not commit on subscriber", default=False)
        p.add_option("--skip-wait", action="store_true", dest="skip_wait",
                help = "do not wait for undo persistence", default=False)
        return p


#
# Provider commands
#

class ProviderSetup(CommonSetup):

    def admin(self):
        cmd = self.args[2]
        if cmd == "tables":
            self.provider_show_tables()
        elif cmd == "add":
            self.provider_add_tables(self.args[3:])
        elif cmd == "remove":
            self.provider_remove_tables(self.args[3:])
        elif cmd == "add-seq":
            self.provider_add_seq_list(self.args[3:])
        elif cmd == "remove-seq":
            self.provider_remove_seq_list(self.args[3:])
        elif cmd == "install":
            self.provider_install()
        elif cmd == "seqs":
            self.provider_list_seqs()
        elif cmd == "curr-tick":
            self.provider_curr_tick()
        else:
            self.log.error('bad subcommand')
            sys.exit(1)

    def provider_list_seqs(self):
        list = self.get_provider_seqs()
        for seq in list:
            print seq

    def provider_get_all_seqs(self):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        list = self.get_all_seqs(src_curs)
        src_db.commit()
        return list

    def provider_add_seq_list(self, seq_list):
        if not seq_list and self.options.all:
            seq_list = self.provider_get_all_seqs()

        cur_list = self.get_provider_seqs()
        gotnew = False
        for seq in seq_list:
            seq = skytools.fq_name(seq)
            if seq in cur_list:
                self.log.info('Seq %s already subscribed' % seq)
                continue
            gotnew = True
            self.provider_add_seq(seq)

        #if gotnew:
        #    self.provider_notify_change()

    def provider_remove_seq_list(self, seq_list):
        if not seq_list and self.options.all:
            seq_list = self.get_provider_seqs()

        for seq in seq_list:
            self.provider_remove_seq(seq)
        self.provider_notify_change()

    def provider_install(self):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        install_provider(src_curs, self.log)

        # create event queue
        q = "select pgq.create_queue(%s)"
        self.exec_provider(q, [self.pgq_queue_name])

    def find_missing_provider_tables(self, pattern='*'):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        q = """select schemaname || '.' || tablename as full_name from pg_tables
                where schemaname not in ('pgq', 'londiste', 'pg_catalog', 'information_schema')
                  and schemaname !~ 'pg_.*'
                  and (schemaname || '.' || tablename) ~ %s
                except select table_name from londiste.provider_get_table_list(%s)"""
        src_curs.execute(q, [glob2regex(pattern), self.pgq_queue_name])
        rows = src_curs.fetchall()
        src_db.commit()
        list = []
        for row in rows:
            list.append(row[0])
        return list
                
    def provider_add_tables(self, table_list):
        self.check_provider_queue()

        if self.options.all and not table_list:
            table_list = ['*.*']

        cur_list = self.get_provider_table_list()
        for tbl in table_list:
            tbls = self.find_missing_provider_tables(skytools.fq_name(tbl))
            
            for tbl in tbls:
                if tbl not in cur_list:
                    self.log.info('Adding %s' % tbl)
                    self.provider_add_table(tbl)
                else:
                    self.log.info("Table %s already added" % tbl)
        #self.provider_notify_change()

    def provider_remove_tables(self, table_list):
        self.check_provider_queue()

        cur_list = self.get_provider_table_list()
        if not table_list and self.options.all:
            table_list = cur_list

        for tbl in table_list:
            tbls = self.get_provider_table_list(skytools.fq_name(tbl))
            for tbl in tbls:
                if tbl not in cur_list:
                    self.log.info('%s already removed' % tbl)
                else:
                    self.log.info("Removing %s" % tbl)
                    self.provider_remove_table(tbl)
        self.provider_notify_change()

    def provider_add_table(self, tbl):

        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        pg_vers = src_curs.connection.server_version

        q = "select londiste.provider_add_table(%s, %s)"
        self.exec_provider(q, [self.pgq_queue_name, tbl])

        # detect dangerous triggers
        if pg_vers >= 90100:
            q = """
            select tg.trigger_name
                from londiste.provider_table tbl,
                     information_schema.triggers tg
                where tbl.queue_name = %s
                  and tbl.table_name = %s
                  and tg.event_object_schema = %s
                  and tg.event_object_table = %s
                  and tg.action_timing = 'AFTER'
                  and tg.trigger_name != tbl.trigger_name
                  and tg.trigger_name < tbl.trigger_name
                  and substring(tg.trigger_name from 1 for 10) != '_londiste_'
                  and substring(tg.trigger_name from char_length(tg.trigger_name) - 6) != '_logger'
            """
        else:
            q = """
            select tg.trigger_name
                from londiste.provider_table tbl,
                     information_schema.triggers tg
                where tbl.queue_name = %s
                  and tbl.table_name = %s
                  and tg.event_object_schema = %s
                  and tg.event_object_table = %s
                  and tg.condition_timing = 'AFTER'
                  and tg.trigger_name != tbl.trigger_name
                  and tg.trigger_name < tbl.trigger_name
                  and substring(tg.trigger_name from 1 for 10) != '_londiste_'
                  and substring(tg.trigger_name from char_length(tg.trigger_name) - 6) != '_logger'
            """

        sname, tname = skytools.fq_name_parts(tbl)
        src_curs.execute(q, [self.pgq_queue_name, tbl, sname, tname])
        for r in src_curs.fetchall():
            self.log.warning("Table %s has AFTER trigger '%s' which runs before Londiste trigger.  "\
                             "If it modifies data, then events will appear in queue in wrong order." % (
                                 tbl, r[0]))
        src_db.commit()

    def provider_remove_table(self, tbl):
        q = "select londiste.provider_remove_table(%s, %s)"
        self.exec_provider(q, [self.pgq_queue_name, tbl])

    def provider_show_tables(self):
        self.check_provider_queue()
        list = self.get_provider_table_list()
        for tbl in list:
            print tbl

    def provider_notify_change(self):
        q = "select londiste.provider_notify_change(%s)"
        self.exec_provider(q, [self.pgq_queue_name])

    def provider_add_seq(self, seq):
        seq = skytools.fq_name(seq)
        q = "select londiste.provider_add_seq(%s, %s)"
        self.exec_provider(q, [self.pgq_queue_name, seq])

    def provider_remove_seq(self, seq):
        seq = skytools.fq_name(seq)
        q = "select londiste.provider_remove_seq(%s, %s)"
        self.exec_provider(q, [self.pgq_queue_name, seq])

    def provider_curr_tick(self):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        q = "select last_tick from pgq.get_consumer_info(%s, %s)"

        src_curs.execute(q, [self.pgq_queue_name, self.consumer_id])
        last_tick = src_curs.fetchone()[0]

        src_db.commit()

        print last_tick

    def exec_provider(self, sql, args):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()

        self.set_lock_timeout(src_curs)

        src_curs.execute(sql, args)

        if self.fake:
            src_db.rollback()
        else:
            src_db.commit()

#
# Subscriber commands
#

class SubscriberSetup(CommonSetup):

    def admin(self):
        cmd = self.args[2]
        if cmd == "tables":
            self.subscriber_show_tables()
        elif cmd == "missing":
            self.subscriber_missing_tables()
        elif cmd == "add":
            self.subscriber_add_tables(self.args[3:])
        elif cmd == "remove":
            self.subscriber_remove_tables(self.args[3:])
        elif cmd == "resync":
            self.subscriber_resync_tables(self.args[3:])
        elif cmd == "register":
            self.subscriber_register()
        elif cmd == "unregister":
            self.subscriber_unregister()
        elif cmd == "install":
            self.subscriber_install()
        elif cmd == "check":
            self.check_tables(self.get_provider_table_list())
        elif cmd == "check-existing":
            ret = self.check_tables(self.get_subscriber_table_list())
            sys.exit(ret)
        elif cmd == "check-undo":
            tables_res = self.check_tables_undo(self.get_subscriber_table_list())
            londiste_res = self.check_londiste_undo()
            sys.exit(tables_res or londiste_res)
        elif cmd in ["fkeys", "triggers"]:
            self.collect_meta(self.get_provider_table_list(), cmd, self.args[3:])
        elif cmd == "seqs":
            self.subscriber_list_seqs()
        elif cmd == "add-seq":
            self.subscriber_add_seq(self.args[3:])
        elif cmd == "remove-seq":
            self.subscriber_remove_seq(self.args[3:])
        elif cmd == "restore-triggers":
            self.restore_triggers(self.args[3], self.args[4:])
        elif cmd == "undo":
            self.subscriber_undo(self.args[3:])
        elif cmd == "add-undo-all":
            self.subscriber_add_undo_all()
        elif cmd == "remove-undo-all":
            self.subscriber_remove_undo_all()
        elif cmd == "curr-tick":
            self.subscriber_curr_tick()
        else:
            self.log.error('bad subcommand: ' + cmd)
            sys.exit(1)

    def collect_meta(self, table_list, meta, args):
        """Display fkey/trigger info."""

        if args == []:
            args = ['pending', 'active']
            
        field_map = {'triggers': ['table_name', 'trigger_name', 'trigger_def'],
                     'fkeys': ['from_table', 'to_table', 'fkey_name', 'fkey_def']}
        
        query_map = {'pending': "select %s from londiste.subscriber_get_table_pending_%s(%%s)",
                     'active' : "select %s from londiste.find_table_%s(%%s)"}

        table_list = self.clean_subscriber_tables(table_list)
        if len(table_list) == 0:
            self.log.info("No tables, no fkeys")
            return

        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()

        for which in args:
            union_list = []
            fields = field_map[meta]
            q = query_map[which] % (",".join(fields), meta)
            for tbl in table_list:
                union_list.append(q % skytools.quote_literal(tbl))

            # use union as fkey may appear in duplicate
            sql = " union ".join(union_list) + " order by 1"
            desc = "%s %s" % (which, meta)
            self.display_table(desc, dst_curs, fields, sql)
        dst_db.commit()

    def display_table(self, desc, curs, fields, sql, args = []):
        """Display multirow query as a table."""

        curs.execute(sql, args)
        rows = curs.dictfetchall()
        if len(rows) == 0:
            return 0
        
        widths = [15] * len(fields)
        for row in rows:
            for i, k in enumerate(fields):
                widths[i] = widths[i] > len(row[k]) and widths[i] or len(row[k])
        widths = [w + 2 for w in widths]

        fmt = '%%-%ds' * (len(widths) - 1) + '%%s'
        fmt = fmt % tuple(widths[:-1])
        print desc
        print fmt % tuple(fields)
        print fmt % tuple(['-'*15] * len(fields))
            
        for row in rows:
            print fmt % tuple([row[k] for k in fields])
        print '\n'
        return 1

    def clean_subscriber_tables(self, table_list):
        """Returns fully-quelifies table list of tables
        that are registered on subscriber.
        """
        subscriber_tables = self.get_subscriber_table_list()
        if not table_list and self.options.all:
            table_list = subscriber_tables
        else:
            newlist = []
            for tbl in table_list:
                tbl = skytools.fq_name(tbl)
                if tbl in subscriber_tables:
                    newlist.append(tbl)
                else:
                    #self.log.warning("table %s not subscribed" % tbl)
                    pass
            table_list = newlist
        return table_list

    def check_tables(self, table_list):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()

        failed = 0
        for tbl in table_list:
            self.log.info("Checking '%s'" % tbl)
            if not skytools.exists_table(src_curs, tbl):
                self.log.warning("Table '%s' missing from provider side" % tbl)
                failed += 1
            elif not skytools.exists_table(dst_curs, tbl):
                self.log.warning("Table '%s' missing from subscriber side" % tbl)
                failed += 1
            else:
                failed += self.check_table_columns(src_curs, dst_curs, tbl)

        src_db.commit()
        dst_db.commit()

        return failed

    def check_londiste_undo(self):
        src_db = self.get_database('subscriber_db')
        curs = src_db.cursor()

        q = """
        select
            max(txtime) - min(txtime) < interval '1 day'
        from londiste_undo.undo_log
        """

        curs.execute(q)
        if curs.fetchone()[0]:
            return 0

        self.log.info("Minimum undo entry is too old")

        return 1

    def check_tables_undo(self, table_list):
        src_db = self.get_database('subscriber_db')
        src_curs = src_db.cursor()

        q = """
        select count(1) from pg_trigger
        where tgname = 'xx99_londiste_undo'
              and tgenabled = 'O'
              and tgrelid = %s::regclass
        """

        failed = 0
        for tbl in table_list:
            # check metainformation
            if not skytools.exists_undo_trigger(src_curs, tbl):
                self.log.info("No undo for '%s'" % tbl)
                failed += 1

            # check real trigger
            src_curs.execute(q, [tbl])
            if src_curs.fetchone()[0] != 1:
                self.log.info("No undo trigger for '%s'" % tbl)
                failed += 1

        src_db.commit()

        return failed

    def restore_triggers(self, tbl, triggers=None):
        tbl = skytools.fq_name(tbl)
        if tbl not in self.get_subscriber_table_list():
            self.log.error("Table %s is not in the subscriber queue." % tbl)
            sys.exit(1)
            
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        
        if not triggers:
            q = "select count(1) from londiste.subscriber_get_table_pending_triggers(%s)"
            dst_curs.execute(q, [tbl])
            if not dst_curs.fetchone()[0]:
                self.log.info("No pending triggers found for %s." % tbl)
            else:
                q = "select londiste.subscriber_restore_all_table_triggers(%s)"
                dst_curs.execute(q, [tbl])
        else:
            for trigger in triggers:
                q = "select count(1) from londiste.find_table_triggers(%s) where trigger_name=%s"
                dst_curs.execute(q, [tbl, trigger])
                if dst_curs.fetchone()[0]:
                    self.log.info("Trigger %s on %s is already active." % (trigger, tbl))
                    continue
                    
                q = "select count(1) from londiste.subscriber_get_table_pending_triggers(%s) where trigger_name=%s"
                dst_curs.execute(q, [tbl, trigger])
                if not dst_curs.fetchone()[0]:
                    self.log.info("Trigger %s not found on %s" % (trigger, tbl))
                    continue
                    
                q = "select londiste.subscriber_restore_table_trigger(%s, %s)"
                dst_curs.execute(q, [tbl, trigger])
        dst_db.commit()

    def check_table_columns(self, src_curs, dst_curs, tbl):
        src_colrows = find_column_types(src_curs, tbl)
        dst_colrows = find_column_types(dst_curs, tbl)

        src_cols = make_type_string(src_colrows)
        dst_cols = make_type_string(dst_colrows)

        err = 0

        if src_cols.find('k') < 0:
            self.log.error("provider table '%s' has no primary key (%s)" % (
                             tbl, src_cols))
            err = 1
        if dst_cols.find('k') < 0:
            self.log.error("subscriber table '%s' has no primary key (%s)" % (
                             tbl, dst_cols))
            err = 1

        trg = self.get_provider_trigger(tbl)

        if trg:
            if trg['tgargs'][1] != src_cols:
                self.log.warning("table '%s' trigger '%s' has wrong schema '%s', required '%s'"
                                 % (tbl, trg['trigger_name'], trg['tgargs'][1], src_cols))
                err = 1
        else:
            self.log.warning("table '%s' missed trigger" % tbl)
            err = 1

        if src_colrows != dst_colrows:
            self.log.warning("table '%s' structure is not same" % tbl)
            for src_col, dst_col in map(None, src_colrows, dst_colrows):
                if src_col is None:
                    self.log.warning("    column '%s' missed in provider table" % (dst_col['name']))
                elif dst_col is None:
                    self.log.warning("    column '%s' missed in subscriber table" % (src_col['name']))
                else:
                    if src_col['name'] != dst_col['name']:
                        self.log.warning("    name mismatch '%s' in provider table, '%s' in subscriber table"
                                         % (src_col['name'], dst_col['name']))
                    if src_col['type'] != dst_col['type']:
                        self.log.warning(
                            "    internal type column '%s' mismatch "
                            "- '%s' in provider table, '%s' in subscriber table"
                            % (src_col['name'], src_col['type'], dst_col['type']))
                    if src_col['rtype'] != dst_col['rtype']:
                        self.log.warning(
                            "    column '%s' type mismatch - '%s' in provider table, '%s' in subscriber table"
                            % (src_col['name'], src_col['rtype'], dst_col['rtype']))

            err = 1

        return err

    def get_subscriber_undo_hooks(self):
        q_subscriber_hooks = "select o_func from londiste_undo.subscriber_undo_hooks(%s)"
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        dst_curs.execute(q_subscriber_hooks, [self.consumer_id])
        list = dst_curs.fetchall()
        dst_db.commit()
        res = []
        for row in list:
            res.append(row[0])
        return res

    def get_provider_undo_hooks(self):
        q_provider_hooks = "select o_func from londiste_undo.provider_undo_hooks(%s)"
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        src_curs.execute(q_provider_hooks, [self.consumer_id])
        list = src_curs.fetchall()
        src_db.commit()
        res = []
        for row in list:
            res.append(row[0])
        return res

    def subscriber_install(self):
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()

        install_subscriber(dst_curs, self.log)

        if self.fake:
            self.log.debug('rollback')
            dst_db.rollback()
        else:
            self.log.debug('commit')
            dst_db.commit()

    def subscriber_register(self):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        src_curs.execute("select pgq.register_consumer(%s, %s)",
            [self.pgq_queue_name, self.consumer_id])
        src_db.commit()

    def subscriber_unregister(self):
        q = "select londiste.subscriber_set_table_state(%s, %s, NULL, NULL)"

        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        tbl_rows = self.fetch_subscriber_tables(dst_curs)
        for row in tbl_rows:
            dst_curs.execute(q, [self.pgq_queue_name, row['table_name']])
        dst_db.commit()

        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        src_curs.execute("select pgq.unregister_consumer(%s, %s)",
            [self.pgq_queue_name, self.consumer_id])
        src_db.commit()

    def subscriber_show_tables(self):
        """print out subscriber table list, with state and snapshot"""
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        list = self.fetch_subscriber_tables(dst_curs)
        dst_db.commit()

        format = "%-30s   %20s"
        print format % ("Table", "State")
        for tbl in list:
            print format % (tbl['table_name'],
                            tbl['merge_state'] or '-')

    def subscriber_missing_tables(self):
        provider_tables = self.get_provider_table_list()
        subscriber_tables = self.get_subscriber_table_list()
        for tbl in provider_tables:
            if tbl not in subscriber_tables:
                print "Table: %s" % tbl
        provider_seqs = self.get_provider_seqs()
        subscriber_seqs = self.get_subscriber_seq_list()
        for seq in provider_seqs:
            if seq not in subscriber_seqs:
                print "Sequence: %s" % seq

    def find_missing_subscriber_tables(self, pattern='*'):
        src_db = self.get_database('subscriber_db')
        src_curs = src_db.cursor()
        q = """select schemaname || '.' || tablename as full_name from pg_tables
                where schemaname not in ('pgq', 'londiste', 'pg_catalog', 'information_schema')
                  and schemaname !~ 'pg_.*'
                  and schemaname || '.' || tablename ~ %s
                except select table_name from londiste.provider_get_table_list(%s)"""
        src_curs.execute(q, [glob2regex(pattern), self.pgq_queue_name])
        rows = src_curs.fetchall()
        src_db.commit()
        list = []
        for row in rows:
            list.append(row[0])
        return list

    def subscriber_add_tables(self, table_list):
        provider_tables = self.get_provider_table_list()
        subscriber_tables = self.get_subscriber_table_list()

        if not table_list and self.options.all:
            table_list = ['*.*']
            for tbl in provider_tables:
                if tbl not in subscriber_tables:
                    table_list.append(tbl)
        
        tbls = []
        for tbl in table_list:
            more = self.find_missing_subscriber_tables(skytools.fq_name(tbl))
            if more == []:
                self.log.info("No tables found that match %s" % tbl)
            tbls.extend(more)
        tbls = list(set(tbls))

        err = 0
        table_list = []
        for tbl in tbls:
            if tbl not in provider_tables:
                err = 1
                self.log.error("Table %s not attached to queue" % tbl)
                if not self.options.force:
                    continue
            table_list.append(tbl)
                
        if err:
            if self.options.force:
                self.log.warning('--force used, ignoring errors')

        err = self.check_tables(table_list)
        if err:
            if self.options.force:
                self.log.warning('--force used, ignoring errors')
            else:
                sys.exit(1)

        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        for tbl in table_list:
            if tbl in subscriber_tables:
                self.log.info("Table %s already added" % tbl)
            else:
                self.log.info("Adding %s" % tbl)
                self.subscriber_add_one_table(dst_curs, tbl)
            dst_db.commit()

    def subscriber_remove_tables(self, table_list):
        subscriber_tables = self.get_subscriber_table_list()
        if not table_list and self.options.all:
            table_list = ['*.*']
            
        for tbl in table_list:
            tbls = self.get_subscriber_table_list(skytools.fq_name(tbl))
            for tbl in tbls:
                if tbl in subscriber_tables:
                    self.log.info("Removing: %s" % tbl)
                    self.subscriber_remove_one_table(tbl)
                else:
                    self.log.info("Table %s already removed" % tbl)

    def subscriber_resync_tables(self, table_list):
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        list = self.fetch_subscriber_tables(dst_curs)

        if not table_list and self.options.all:
            table_list = self.get_subscriber_table_list()

        for tbl in table_list:
            tbl = skytools.fq_name(tbl)
            tbl_row = None
            for row in list:
                if row['table_name'] == tbl:
                    tbl_row = row
                    break
            if not tbl_row:
                self.log.warning("Table %s not found" % tbl)
            elif tbl_row['merge_state'] != 'ok':
                self.log.warning("Table %s is not in stable state" % tbl)
            else:
                self.log.info("Resyncing %s" % tbl)
                q = "select londiste.subscriber_set_table_state(%s, %s, NULL, NULL)"
                dst_curs.execute(q, [self.pgq_queue_name, tbl])
        dst_db.commit()

    def subscriber_add_one_table(self, dst_curs, tbl):
        drop_triggers_type = ['c'] # drop only constraint triggers
        q_add = "select londiste.subscriber_add_table(%s, %s)"
        q_triggers = "select londiste.subscriber_drop_all_table_triggers(%s, %s)"
        q_disable_undo = "select londiste_undo.disable_trigger(%s, %s)"

        if self.options.expect_sync and self.options.skip_truncate:
            self.log.error("Too many options: --expect-sync and --skip-truncate")
            sys.exit(1)

        (schema_name, table_name) = normalize_table_name(dst_curs, tbl)

        dst_curs.execute(q_disable_undo, [schema_name, table_name])
        table_name = dst_curs.fetchone()[0]
        self.log.info("Disabled UNDO trigger on %s" % table_name)

        dst_curs.execute(q_add, [self.pgq_queue_name, tbl])
        if self.options.expect_sync:
            q = "select londiste.subscriber_set_table_state(%s, %s, null, 'ok')"
            dst_curs.execute(q, [self.pgq_queue_name, tbl])
            return

        dst_curs.execute(q_triggers, [tbl, drop_triggers_type])
        if self.options.skip_truncate:
            q = "select londiste.subscriber_set_skip_truncate(%s, %s, true)"
            dst_curs.execute(q, [self.pgq_queue_name, tbl])

    def subscriber_remove_one_table(self, tbl):
        q_remove = "select londiste.subscriber_remove_table(%s, %s)"
        q_triggers = "select londiste.subscriber_restore_all_table_triggers(%s)"
        q_undo = "select londiste_undo.remove_trigger(%s, %s)"

        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        (schema_name, table_name) = normalize_table_name(dst_curs, tbl)
        dst_curs.execute(q_remove, [self.pgq_queue_name, tbl])
        dst_curs.execute(q_triggers, [tbl])
        dst_curs.execute(q_undo, [schema_name, table_name])
        dst_db.commit()

    def get_subscriber_seq_list(self):
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        q = "SELECT * from londiste.subscriber_get_seq_list(%s)"
        dst_curs.execute(q, [self.pgq_queue_name])
        list = dst_curs.fetchall()
        dst_db.commit()
        res = []
        for row in list:
            res.append(row[0])
        return res

    def subscriber_list_seqs(self):
        list = self.get_subscriber_seq_list()
        for seq in list:
            print seq

    def subscriber_add_seq(self, seq_list):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        
        prov_list = self.get_provider_seqs()

        full_list = self.get_all_seqs(dst_curs)
        cur_list = self.get_subscriber_seq_list()

        if not seq_list and self.options.all:
            seq_list = prov_list
        
        for seq in seq_list:
            seq = skytools.fq_name(seq)
            if seq not in prov_list:
                self.log.error('Seq %s does not exist on provider side' % seq)
                continue
            if seq not in full_list:
                self.log.error('Seq %s does not exist on subscriber side' % seq)
                continue
            if seq in cur_list:
                self.log.info('Seq %s already subscribed' % seq)
                continue

            self.log.info('Adding sequence: %s' % seq)
            q = "select londiste.subscriber_add_seq(%s, %s)"
            dst_curs.execute(q, [self.pgq_queue_name, seq])

        dst_db.commit()

    def subscriber_remove_seq(self, seq_list):
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        cur_list = self.get_subscriber_seq_list()

        if not seq_list and self.options.all:
            seq_list = cur_list

        for seq in seq_list:
            seq = skytools.fq_name(seq)
            if seq not in cur_list:
                self.log.warning('Seq %s not subscribed')
            else:
                self.log.info('Removing sequence: %s' % seq)
                q = "select londiste.subscriber_remove_seq(%s, %s)"
                dst_curs.execute(q, [self.pgq_queue_name, seq])
        dst_db.commit()

    def subscriber_undo(self, arg_list):
        q_provider_tick = "select last_tick from pgq.get_consumer_info(%s, %s)"
        q_undo = "select londiste_undo.run_undo(%s, %s, true)"
        q_get_undo = "select * from londiste_undo.get_temp_applied_undo(%s, %s)"
        q_save_applied_undo = "select londiste_undo.move_temp_to_applied()"
        keep_tick_id = None
        pidfile = self.cf.get("pidfile")
        provider_undo_hooks = self.get_provider_undo_hooks()
        subscriber_undo_hooks = self.get_subscriber_undo_hooks()
        undo_fields = [
            'id', 'txtime', 'consumer_name',
            'dst_schema', 'dst_table',
            'undo_cmd', 'cmd_data', 'cmd_pk'
        ]
        tmpl_q_provider_hook = "select %s (" \
                               + ', '.join(['%%%%(%s)s' % f for f in undo_fields]) \
                               + ") as res"

        if not pidfile:
            self.log.warning("No pidfile in config, cannot check for running londiste")
        elif os.path.isfile(pidfile):
            if skytools.signal_pidfile(pidfile, 0):
                self.log.error("londiste (%s) already running, stop it first" % pidfile)
                sys.exit(1)

        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()

        self.log.info("Last tick_id from subscriber: %d" % self.get_subscriber_curr_tick())

        if len(arg_list) == 0:
            src_curs.execute(q_provider_tick, [self.pgq_queue_name, self.consumer_id])
            last_tick = src_curs.fetchone()[0]
            src_db.commit()
            keep_tick_id = last_tick
            self.log.info("Using tick_id from master: %d" % keep_tick_id)
        else:
            keep_tick_id = int(arg_list[0])

        self.log.info('Run undo till tick_id: %d' % keep_tick_id)

        if subscriber_undo_hooks:
            self.log.info('Subscriber undo hooks: %s' % subscriber_undo_hooks)

        dst_curs.execute(q_undo, [self.consumer_id, keep_tick_id])
        cnt = dst_curs.fetchone()[0]
        for msg in dst_db.notices:
            self.log.info("PG %s" % msg.rstrip())
        self.log.info("Undo %s rows" % cnt)

        if provider_undo_hooks:
            self.log.info('Provider undo hooks: %s' % provider_undo_hooks)
            # londiste is switch off now, so hook result will be played after it started
            # (and after undo is compleated)
            dst_curs.execute(q_get_undo, [self.consumer_id, keep_tick_id])
            self.log.debug("%s: %d" % (dst_curs.query, dst_curs.rowcount))
            for row in dst_curs:
                for hook_name in provider_undo_hooks:
                    hook = tmpl_q_provider_hook % hook_name
                    src_curs.execute(hook, row)
                    for msg in src_db.notices:
                        self.log.info("PG provider %s" % msg.rstrip())
                    del src_db.notices[:]
            # provider _must_ be commited first, we can call hooks again,
            # but not after subscriber undo commited
            src_db.commit()

        dst_curs.execute(q_save_applied_undo)

        if self.options.dry_run:
            dst_db.rollback()
        else:
            dst_db.commit()

    def subscriber_add_undo_all(self):
        subscriber_tables = self.get_subscriber_table_list()
        subscriber_child_tables = self.get_subscriber_child_tables_list(subscriber_tables)
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        q_add_undo = "select londiste_undo.enable_trigger(%s, %s, %s, %s)"
        q_lock_consumer = "lock londiste.completed"

        self.log.info("Lock consumer ...")
        dst_curs.execute(q_lock_consumer)
        self.log.info("ok")

        for tbl in subscriber_tables + subscriber_child_tables:
            (schema_name, table_name) = normalize_table_name(dst_curs, tbl)
            cols_info = find_column_types(dst_curs, tbl)

            pk = filter(lambda x: x[1] == 'k', cols_info)

            if len(pk) != 1:
                self.log.error("Unknown PK for %s.%s" % (schema_name, table_name))
                sys.exit(1)
            pk = pk[0][0]

            is_child = 'CHILD ' if tbl in subscriber_child_tables else ''

            self.log.info("Adding UNDO trigger on %s%s.%s(%s)" % (is_child, schema_name, table_name, pk))
            dst_curs.execute(q_add_undo, [self.consumer_id, schema_name, table_name, pk])

        dst_db.commit()

        if not self.options.skip_wait:
            self.wait_undo_enough()

    def subscriber_remove_undo_all(self):
        subscriber_tables = self.get_subscriber_table_list()
        subscriber_child_tables = self.get_subscriber_child_tables_list(subscriber_tables)
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        q_clean_all_undo = "select londiste_undo.clean_all_undo()"
        q_remove_undo = "select londiste_undo.remove_trigger(%s, %s, %s)"
        q_lock_consumer = "lock londiste.completed"
        force = False

        self.log.info("Lock consumer ...")
        dst_curs.execute(q_lock_consumer)
        self.log.info("ok")

        dst_curs.execute(q_clean_all_undo)
        self.log.info("All UNDO cleaned")

        if self.options.force:
            self.log.warning('--force used, ignoring non exists triggers')
            force = True

        for tbl in subscriber_tables + subscriber_child_tables:
            (schema_name, table_name) = normalize_table_name(dst_curs, tbl)

            is_child = 'CHILD ' if tbl in subscriber_child_tables else ''

            self.log.info("Removing UNDO trigger on %s%s.%s" % (is_child, schema_name, table_name))
            dst_curs.execute(q_remove_undo, [schema_name, table_name, force])

        for msg in dst_db.notices:
            self.log.info("PG %s" % msg.rstrip())

        dst_db.commit()

    def get_subscriber_curr_tick(self):
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()
        q = "select londiste.get_last_tick(%s)"

        dst_curs.execute(q, [self.consumer_id])
        last_tick = dst_curs.fetchone()[0]

        dst_db.commit()

        return last_tick

    def subscriber_curr_tick(self):
        print self.get_subscriber_curr_tick()

    def wait_undo_enough(self):
        src_db = self.get_database('provider_db')
        src_curs = src_db.cursor()
        stdb_db = self.get_database('provider_standby_db')
        stdb_curs = stdb_db.cursor()
        dst_db = self.get_database('subscriber_db')
        dst_curs = dst_db.cursor()

        q_src_last_tick = "select last_tick from pgq.get_consumer_info(%s, %s)"
        q_dst_last_tick = "select londiste.get_last_tick(%s)"

        src_curs.execute(q_src_last_tick, [self.pgq_queue_name, self.consumer_id])
        last_tick = src_curs.fetchone()[0]
        src_db.commit()

        stdb_curs.execute(q_src_last_tick, [self.pgq_queue_name, self.consumer_id])
        stdb_last_tick = stdb_curs.fetchone()[0]
        stdb_db.commit()

        self.log.info("Wait tick %s on standby (curr: %s)..." % (last_tick, stdb_last_tick))
        [h.flush() for h in self.log.handlers]

        while stdb_last_tick < last_tick:
            time.sleep(5)
            stdb_curs.execute(q_src_last_tick, [self.pgq_queue_name, self.consumer_id])
            stdb_last_tick = stdb_curs.fetchone()[0]
            stdb_db.commit()

        dst_curs.execute(q_dst_last_tick, [self.consumer_id])
        dst_last_tick = dst_curs.fetchone()[0]
        dst_db.commit()

        self.log.info("OK, wait tick %s on subscriber (curr: %s)..." % (last_tick, dst_last_tick))
        [h.flush() for h in self.log.handlers]

        while dst_last_tick < last_tick:
            time.sleep(5)
            dst_curs.execute(q_dst_last_tick, [self.consumer_id])
            dst_last_tick = dst_curs.fetchone()[0]
            dst_db.commit()

        self.log.info("OK, undo stored persistently and available for undo till %s tick" % (last_tick))

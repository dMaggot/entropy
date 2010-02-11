# -*- coding: utf-8 -*-
"""

    @author: Fabio Erculiani <lxnay@sabayon.org>
    @contact: lxnay@sabayon.org
    @copyright: Fabio Erculiani
    @license: GPL-2

    B{Entropy Package Manager Client Miscellaneous functions Interface}.

"""
import os
import bz2
import stat
import fcntl
import errno
import sys
import shutil
import time
import subprocess
import tempfile
from datetime import datetime

from entropy.i18n import _
from entropy.const import etpConst, const_debug_write, etpSys, \
    const_setup_file, initconfig_entropy_constants, const_pid_exists, \
    const_set_nice_level, const_setup_perms, const_setup_entropy_pid, \
    const_isstring, const_convert_to_unicode
from entropy.exceptions import RepositoryError, InvalidPackageSet,\
    SystemDatabaseError
from entropy.db import EntropyRepository
from entropy.cache import EntropyCacher
from entropy.client.interfaces.db import ClientEntropyRepositoryPlugin
from entropy.output import purple, bold, red, blue, darkgreen, darkred, brown

from entropy.db.exceptions import IntegrityError, OperationalError, \
    DatabaseError

import entropy.tools

class RepositoryMixin:

    def validate_repositories(self, quiet = False):
        self.MirrorStatus.clear()
        self._repo_error_messages_cache.clear()

        # clear live masking validation cache, if exists
        cl_id = self.sys_settings_client_plugin_id
        client_metadata = self.SystemSettings.get(cl_id, {})
        if "masking_validation" in client_metadata:
            client_metadata['masking_validation']['cache'].clear()

        # valid repositories
        del self._enabled_repos[:]
        for repoid in self.SystemSettings['repositories']['order']:
            # open database
            try:

                dbc = self.open_repository(repoid)
                dbc.listConfigProtectEntries()
                dbc.validateDatabase()
                self._enabled_repos.append(repoid)

            except RepositoryError:

                if quiet:
                    continue

                t = _("Repository") + " " + const_convert_to_unicode(repoid) \
                    + " " + _("is not available") + ". " + _("Cannot validate")
                t2 = _("Please update your repositories now in order to remove this message!")
                self.output(
                    darkred(t),
                    importance = 1,
                    type = "warning"
                )
                self.output(
                    purple(t2),
                    header = bold("!!! "),
                    importance = 1,
                    type = "warning"
                )
                continue # repo not available
            except (OperationalError, DatabaseError, SystemDatabaseError,):

                if quiet:
                    continue

                t = _("Repository") + " " + repoid + " " + \
                    _("is corrupted") + ". " + _("Cannot validate")
                self.output(
                                    darkred(t),
                                    importance = 1,
                                    type = "warning"
                                   )
                continue

        # to avoid having zillions of open files when loading a lot of EquoInterfaces
        self.close_all_repositories(mask_clear = False)

    def __get_repository_cache_key(self, repoid):
        return (repoid, etpConst['systemroot'],)

    def _init_generic_temp_repository(self, repoid, description,
        package_mirrors = None):
        if package_mirrors is None:
            package_mirrors = []

        dbc = self.open_temp_repository(dbname = repoid)
        repo_key = self.__get_repository_cache_key(repoid)
        self._memory_db_instances[repo_key] = dbc

        # add to self.SystemSettings['repositories']['available']
        repodata = {
            'repoid': repoid,
            '__temporary__': True,
            'description': description,
            'packages': package_mirrors,
            'dbpath': dbc.dbFile,
        }
        self.add_repository(repodata)
        return dbc

    def close_all_repositories(self, mask_clear = True):
        for item in sorted(self._repodb_cache.keys()):
            # in-memory repositories cannot be closed
            # otherwise everything will be lost, to
            # effectively close these repos you
            # must call remove_repository method
            if item in self._memory_db_instances:
                continue
            try:
                self._repodb_cache.pop(item).closeDB()
            except OperationalError as err: # wtf!
                sys.stderr.write("!!! Cannot close Entropy repos: %s\n" % (
                    err,))
        self._repodb_cache.clear()

        # disable hooks during SystemSettings cleanup
        # otherwise it makes entropy.client.interfaces.repository crazy
        old_value = self._can_run_sys_set_hooks
        self._can_run_sys_set_hooks = False
        if mask_clear:
            self.SystemSettings.clear()
        self._can_run_sys_set_hooks = old_value


    def is_repository_connection_cached(self, repoid):
        if (repoid, etpConst['systemroot'],) in self._repodb_cache:
            return True
        return False

    def open_repository(self, repoid):

        # support for installed pkgs repository, got by issuing
        # repoid = etpConst['clientdbid']
        if repoid == etpConst['clientdbid']:
            return self._installed_repository

        key = self.__get_repository_cache_key(repoid)
        if key not in self._repodb_cache:
            dbconn = self.load_repository_database(repoid,
                xcache = self.xcache, indexing = self.indexing)
            try:
                dbconn.checkDatabaseApi()
            except (OperationalError, TypeError,):
                pass

            self._repodb_cache[key] = dbconn
            return dbconn

        return self._repodb_cache.get(key)

    def load_repository_database(self, repoid, xcache = True, indexing = True):

        if const_isstring(repoid):
            if repoid.endswith(etpConst['packagesext']):
                xcache = False

        repo_data = self.SystemSettings['repositories']['available']
        if repoid not in repo_data:
            t = "%s: %s" % (_("bad repository id specified"), repoid,)
            if repoid not in self._repo_error_messages_cache:
                self.output(
                    darkred(t),
                    importance = 2,
                    type = "warning"
                )
                self._repo_error_messages_cache.add(repoid)
            raise RepositoryError("RepositoryError: %s" % (t,))

        if repo_data[repoid].get('__temporary__'):
            repo_key = self.__get_repository_cache_key(repoid)
            conn = self._memory_db_instances.get(repo_key)
        else:
            dbfile = os.path.join(repo_data[repoid]['dbpath'],
                etpConst['etpdatabasefile'])
            if not os.path.isfile(dbfile):
                t = _("Repository %s hasn't been downloaded yet.") % (repoid,)
                if repoid not in self._repo_error_messages_cache:
                    self.output(
                        darkred(t),
                        importance = 2,
                        type = "warning"
                    )
                    self._repo_error_messages_cache.add(repoid)
                raise RepositoryError("RepositoryError: %s" % (t,))

            conn = EntropyRepository(
                readOnly = True,
                dbFile = dbfile,
                dbname = etpConst['dbnamerepoprefix']+repoid,
                xcache = xcache,
                indexing = indexing
            )
            self._add_plugin_to_client_repository(conn, repoid)

        if (repoid not in self._treeupdates_repos) and \
            (entropy.tools.is_root()) and \
            (not repoid.endswith(etpConst['packagesext'])):
                # only as root due to Portage
                try:
                    updated = self.repository_packages_spm_sync(repoid, conn)
                except (OperationalError, DatabaseError,):
                    updated = False
                if updated:
                    self.Cacher.discard()
                    EntropyCacher.clear_cache_item(
                        EntropyCacher.CACHE_IDS['world_update'])
                    EntropyCacher.clear_cache_item(
                        EntropyCacher.CACHE_IDS['critical_update'])
        return conn

    def get_repository_revision(self, reponame):
        db_data = self.SystemSettings['repositories']['available'][reponame]
        fname = db_data['dbpath']+"/"+etpConst['etpdatabaserevisionfile']
        revision = -1
        if os.path.isfile(fname) and os.access(fname, os.R_OK):
            with open(fname, "r") as f:
                try:
                    revision = int(f.readline().strip())
                except (OSError, IOError, ValueError,):
                    pass
        return revision

    def update_repository_revision(self, reponame):
        r = self.get_repository_revision(reponame)
        db_data = self.SystemSettings['repositories']['available'][reponame]
        db_data['dbrevision'] = "0"
        if r != -1:
            db_data['dbrevision'] = str(r)

    def add_repository(self, repodata):

        product = self.SystemSettings['repositories']['product']
        branch = self.SystemSettings['repositories']['branch']
        avail_data = self.SystemSettings['repositories']['available']
        repoid = repodata['repoid']

        avail_data[repoid] = {}
        avail_data[repoid]['description'] = repodata['description']

        if repoid.endswith(etpConst['packagesext']) or \
            repodata.get('__temporary__'):
            # dynamic repository

            # no need # avail_data[repoid]['plain_packages'] = \
            # repodata['plain_packages'][:]
            avail_data[repoid]['packages'] = repodata['packages'][:]
            smart_package = repodata.get('smartpackage')
            if smart_package != None:
                avail_data[repoid]['smartpackage'] = smart_package

            avail_data[repoid]['dbpath'] = repodata.get('dbpath')
            avail_data[repoid]['pkgpath'] = repodata.get('pkgpath')
            avail_data[repoid]['__temporary__'] = repodata.get('__temporary__')
            # put at top priority, shift others
            self.SystemSettings['repositories']['order'].insert(0, repoid)

        else:

            self.__save_repository_settings(repodata)
            self.SystemSettings._clear_repository_cache(repoid = repoid)
            self.close_all_repositories()
            self.clear_cache()
            self.SystemSettings.clear()

        self.validate_repositories()

    def remove_repository(self, repoid, disable = False):

        done = False
        if repoid in self.SystemSettings['repositories']['available']:
            del self.SystemSettings['repositories']['available'][repoid]
            done = True

        if repoid in self.SystemSettings['repositories']['excluded']:
            del self.SystemSettings['repositories']['excluded'][repoid]
            done = True

        # also early remove from validRepositories to avoid
        # issues when reloading SystemSettings which is bound to Entropy Client
        # SystemSettings plugin, which triggers calculate_world_updates, which
        # triggers _all_repositories_checksum, which triggers open_repository,
        # which triggers load_repository_database, which triggers an unwanted
        # output message => "bad repository id specified"
        if repoid in self._enabled_repos:
            self._enabled_repos.remove(repoid)

        # ensure that all dbs are closed
        self.close_all_repositories()

        if done:

            if repoid in self.SystemSettings['repositories']['order']:
                self.SystemSettings['repositories']['order'].remove(repoid)

            self.SystemSettings._clear_repository_cache(repoid = repoid)
            # save new self.SystemSettings['repositories']['available'] to file
            repodata = {}
            repodata['repoid'] = repoid
            if disable:
                self.__save_repository_settings(repodata, disable = True)
            else:
                self.__save_repository_settings(repodata, remove = True)
            self.SystemSettings.clear()

        repo_mem_key = self.__get_repository_cache_key(repoid)
        mem_inst = self._memory_db_instances.pop(repo_mem_key, None)
        if isinstance(mem_inst, EntropyRepository):
            mem_inst.closeDB()

        # reset db cache
        self.close_all_repositories()
        self.validate_repositories()

    def __save_repository_settings(self, repodata, remove = False,
        disable = False, enable = False):

        if repodata['repoid'].endswith(etpConst['packagesext']):
            return

        content = []
        if os.path.isfile(etpConst['repositoriesconf']):
            f = open(etpConst['repositoriesconf'])
            content = [x.strip() for x in f.readlines()]
            f.close()

        if not disable and not enable:
            content = [x for x in content if not \
                x.startswith("repository|"+repodata['repoid'])]
            if remove:
                # also remove possible disable repo
                content = [x for x in content if not (x.startswith("#") and \
                    not x.startswith("##") and \
                        (x.find("repository|"+repodata['repoid']) != -1))]
        if not remove:

            repolines = [x for x in content if x.startswith("repository|") or \
                (x.startswith("#") and not x.startswith("##") and \
                    (x.find("repository|") != -1))]
            # exclude lines from repolines
            content = [x for x in content if x not in repolines]
            # filter sane repolines lines
            repolines = [x for x in repolines if (len(x.split("|")) == 5)]
            repolines_data = {}
            repocount = 0
            for x in repolines:
                repolines_data[repocount] = {}
                repolines_data[repocount]['repoid'] = x.split("|")[1]
                repolines_data[repocount]['line'] = x
                if disable and x.split("|")[1] == repodata['repoid']:
                    if not x.startswith("#"):
                        x = "#"+x
                    repolines_data[repocount]['line'] = x
                elif enable and x.split("|")[1] == repodata['repoid'] \
                    and x.startswith("#"):
                    repolines_data[repocount]['line'] = x[1:]
                repocount += 1

            if not disable and not enable: # so it's a add

                line = "repository|%s|%s|%s|%s#%s#%s,%s" % (
                    repodata['repoid'],
                    repodata['description'],
                    ' '.join(repodata['plain_packages']),
                    repodata['plain_database'],
                    repodata['dbcformat'],
                    repodata['service_port'],
                    repodata['ssl_service_port'],
                )

                # seek in repolines_data for a disabled entry and remove
                to_remove = set()
                for cc in repolines_data:
                    cc_line = repolines_data[cc]['line']
                    if cc_line.startswith("#") and \
                        (cc_line.find("repository|"+repodata['repoid']) != -1):
                        # then remove
                        to_remove.add(cc)
                for x in to_remove:
                    del repolines_data[x]

                repolines_data[repocount] = {}
                repolines_data[repocount]['repoid'] = repodata['repoid']
                repolines_data[repocount]['line'] = line

            # inject new repodata
            keys = sorted(repolines_data.keys())
            for cc in keys:
                #repoid = repolines_data[cc]['repoid']
                # write the first
                line = repolines_data[cc]['line']
                content.append(line)

        try:
            repo_conf = etpConst['repositoriesconf']
            tmp_repo_conf = repo_conf + ".cfg_save_set"
            with open(tmp_repo_conf, "w") as tmp_f:
                for line in content:
                    tmp_f.write(line + "\n")
                tmp_f.flush()
            os.rename(tmp_repo_conf, repo_conf)
        except (OSError, IOError,): # permission denied?
            return False
        return True


    def __write_ordered_repositories_entries(self, ordered_repository_list):
        content = []
        if os.path.isfile(etpConst['repositoriesconf']):
            f = open(etpConst['repositoriesconf'])
            content = [x.strip() for x in f.readlines()]
            f.close()

        repolines = [x for x in content if x.startswith("repository|") and \
            (len(x.split("|")) == 5)]
        content = [x for x in content if x not in repolines]
        for repoid in ordered_repository_list:
            # get repoid from repolines
            for x in repolines:
                repoidline = x.split("|")[1]
                if repoid == repoidline:
                    content.append(x)

        repo_conf = etpConst['repositoriesconf']
        tmp_repo_conf = repo_conf + ".cfg_save"
        with open(tmp_repo_conf, "w") as tmp_f:
            for line in content:
                tmp_f.write(line + "\n")
            tmp_f.flush()
        os.rename(tmp_repo_conf, repo_conf)

    def shift_repository(self, repoid, toidx):
        # update self.SystemSettings['repositories']['order']
        self.SystemSettings['repositories']['order'].remove(repoid)
        self.SystemSettings['repositories']['order'].insert(toidx, repoid)
        self.__write_ordered_repositories_entries(
            self.SystemSettings['repositories']['order'])
        self.SystemSettings.clear()
        self.close_all_repositories()
        self.SystemSettings._clear_repository_cache(repoid = repoid)
        self.validate_repositories()

    def enable_repository(self, repoid):
        self.SystemSettings._clear_repository_cache(repoid = repoid)
        # save new self.SystemSettings['repositories']['available'] to file
        repodata = {}
        repodata['repoid'] = repoid
        self.__save_repository_settings(repodata, enable = True)
        self.SystemSettings.clear()
        self.close_all_repositories()
        self.validate_repositories()

    def disable_repository(self, repoid):
        # update self.SystemSettings['repositories']['available']
        done = False
        try:
            del self.SystemSettings['repositories']['available'][repoid]
            done = True
        except:
            pass

        if done:
            try:
                self.SystemSettings['repositories']['order'].remove(repoid)
            except (IndexError,):
                pass
            # it's not vital to reset
            # self.SystemSettings['repositories']['order'] counters

            self.SystemSettings._clear_repository_cache(repoid = repoid)
            # save new self.SystemSettings['repositories']['available'] to file
            repodata = {}
            repodata['repoid'] = repoid
            self.__save_repository_settings(repodata, disable = True)
            self.SystemSettings.clear()

        self.close_all_repositories()
        self.validate_repositories()

    def get_repository_settings(self, repoid):
        try:
            repodata = self.SystemSettings['repositories']['available'][repoid].copy()
        except KeyError:
            if repoid not in self.SystemSettings['repositories']['excluded']:
                raise
            repodata = self.SystemSettings['repositories']['excluded'][repoid].copy()
        return repodata

    # every tbz2 file that would be installed must pass from here
    def add_package_to_repos(self, pkg_file):
        atoms_contained = []
        basefile = os.path.basename(pkg_file)
        db_dir = tempfile.mkdtemp()
        dbfile = os.path.join(db_dir, etpConst['etpdatabasefile'])
        dump_rc = entropy.tools.dump_entropy_metadata(pkg_file, dbfile)
        if not dump_rc:
            return -1, atoms_contained
        # add dbfile
        repodata = {}
        repodata['repoid'] = basefile
        repodata['description'] = "Dynamic database from " + basefile
        repodata['packages'] = []
        repodata['dbpath'] = os.path.dirname(dbfile)
        repodata['pkgpath'] = os.path.realpath(pkg_file) # extra info added
        repodata['smartpackage'] = False # extra info added

        mydbconn = self.open_generic_database(dbfile)
        # read all idpackages
        try:
            # all branches admitted from external files
            myidpackages = mydbconn.listAllIdpackages()
        except (AttributeError, DatabaseError, IntegrityError,
            OperationalError,):
            return -2, atoms_contained

        if len(myidpackages) > 1:
            repodata[basefile]['smartpackage'] = True
        for myidpackage in myidpackages:
            compiled_arch = mydbconn.retrieveDownloadURL(myidpackage)
            if compiled_arch.find("/"+etpConst['currentarch']+"/") == -1:
                return -3, atoms_contained
            atoms_contained.append((int(myidpackage), basefile))

        self.add_repository(repodata)
        self.validate_repositories()
        if basefile not in self._enabled_repos:
            self.remove_repository(basefile)
            return -4, atoms_contained
        mydbconn.closeDB()
        del mydbconn
        return 0, atoms_contained

    def _add_plugin_to_client_repository(self, entropy_client_repository,
        repo_id):
        etp_db_meta = {
            'output_interface': self,
            'repo_name': repo_id,
        }
        repo_plugin = ClientEntropyRepositoryPlugin(self,
            metadata = etp_db_meta)
        entropy_client_repository.add_plugin(repo_plugin)

    def repositories(self):
        """
        Return a list of enabled (and valid) repository identifiers, excluding
        installed packages repository. You can use the identifiers in this list
        to open EntropyRepository instances using Client.open_repository()
        NOTE: this method directly returns a reference to the internal
        enabled repository list object.
        NOTE: the returned list is built based on SystemSettings repository
        metadata but might differ because extra checks are done at runtime.
        So, if you want to iterate over valid repositories, use this method.

        @return: enabled and valid repository identifiers
        @rtype list
        """
        return self._enabled_repos

    def installed_repository(self):
        """
        Return Entropy Client installed packages repository.

        @return: Entropy Client installed packages repository
        @rtype: entropy.db.EntropyRepository
        """
        return self._installed_repository

    def open_installed_repository(self):

        def load_db_from_ram():
            self.safe_mode = etpConst['safemodeerrors']['clientdb']
            mytxt = "%s, %s" % (_("System database not found or corrupted"),
                _("running in safe mode using empty database from RAM"),)
            if not etpSys['unittest']:
                self.output(
                    darkred(mytxt),
                    importance = 1,
                    type = "warning",
                    header = bold(" !!! "),
                )
            m_conn = self.open_temp_repository(dbname = etpConst['clientdbid'])
            self._add_plugin_to_client_repository(m_conn,
                etpConst['clientdbid'])
            return m_conn

        # if we are in unit testing mode (triggered by unit testing
        # code), always use db from ram
        if etpSys['unittest']:
            self._installed_repository = load_db_from_ram()
            return self._installed_repository

        db_dir = os.path.dirname(etpConst['etpdatabaseclientfilepath'])
        if not os.path.isdir(db_dir):
            os.makedirs(db_dir)

        db_path = etpConst['etpdatabaseclientfilepath']
        if (not self.noclientdb) and (not os.path.isfile(db_path)):
            conn = load_db_from_ram()
            entropy.tools.print_traceback(f = self.clientLog)
        else:
            try:
                conn = EntropyRepository(readOnly = False, dbFile = db_path,
                    dbname = etpConst['clientdbid'],
                    xcache = self.xcache, indexing = self.indexing
                )
                self._add_plugin_to_client_repository(conn,
                    etpConst['clientdbid'])
                # TODO: remove this in future, drop useless data from clientdb
            except (DatabaseError,):
                entropy.tools.print_traceback(f = self.clientLog)
                conn = load_db_from_ram()
            else:
                # validate database
                if not self.noclientdb:
                    try:
                        conn.validateDatabase()
                    except SystemDatabaseError:
                        try:
                            conn.closeDB()
                        except:
                            pass
                        entropy.tools.print_traceback(f = self.clientLog)
                        conn = load_db_from_ram()

        self._installed_repository = conn
        return conn

    def reopen_installed_repository(self):
        self._installed_repository.closeDB()
        self.open_installed_repository()
        # make sure settings are in sync
        self.SystemSettings.clear()

    def client_repository_sanity_check(self):
        self.output(
            darkred(_("Sanity Check") + ": " + _("system database")),
            importance = 2,
            type = "warning"
        )
        idpkgs = self._installed_repository.listAllIdpackages()
        length = len(idpkgs)
        count = 0
        errors = False
        scanning_txt = _("Scanning...")
        for x in idpkgs:
            count += 1
            self.output(
                                    darkgreen(scanning_txt),
                                    importance = 0,
                                    type = "info",
                                    back = True,
                                    count = (count, length),
                                    percent = True
                                )
            try:
                self._installed_repository.getPackageData(x)
            except Exception as e:
                entropy.tools.print_traceback()
                errors = True
                self.output(
                    darkred(_("Errors on idpackage %s, error: %s")) % (x, e),
                    importance = 0,
                    type = "warning"
                )

        if not errors:
            t = _("Sanity Check") + ": %s" % (bold(_("PASSED")),)
            self.output(
                darkred(t),
                importance = 2,
                type = "warning"
            )
            return 0
        else:
            t = _("Sanity Check") + ": %s" % (bold(_("CORRUPTED")),)
            self.output(
                darkred(t),
                importance = 2,
                type = "warning"
            )
            return -1

    def open_generic_database(self, dbfile, dbname = None, xcache = None,
            readOnly = False, indexing_override = None, skipChecks = False):
        if xcache == None:
            xcache = self.xcache
        if indexing_override != None:
            indexing = indexing_override
        else:
            indexing = self.indexing
        if dbname == None:
            dbname = etpConst['genericdbid']
        conn = EntropyRepository(
            readOnly = readOnly,
            dbFile = dbfile,
            dbname = dbname,
            xcache = xcache,
            indexing = indexing,
            skipChecks = skipChecks
        )
        self._add_plugin_to_client_repository(conn, dbname)
        return conn

    def open_temp_repository(self, dbname = None):
        if dbname == None:
            dbname = etpConst['genericdbid']

        dbc = EntropyRepository(
            readOnly = False,
            dbFile = entropy.tools.get_random_temp_file(),
            dbname = dbname,
            xcache = False,
            indexing = False,
            skipChecks = True,
            temporary = True
        )
        self._add_plugin_to_client_repository(dbc, dbname)
        dbc.initializeDatabase()
        return dbc

    def backup_database(self, dbpath, backup_dir = None, silent = False,
        compress_level = 9):

        if compress_level not in list(range(1, 10)):
            compress_level = 9

        backup_dir = os.path.dirname(dbpath)
        if not backup_dir: backup_dir = os.path.dirname(dbpath)
        dbname = os.path.basename(dbpath)
        bytes_required = 1024000*300
        if not (os.access(backup_dir, os.W_OK) and \
                os.path.isdir(backup_dir) and os.path.isfile(dbpath) and \
                os.access(dbpath, os.R_OK) and \
                entropy.tools.check_required_space(backup_dir, bytes_required)):
            if not silent:
                mytxt = "%s: %s, %s" % (
                    darkred(_("Cannot backup selected database")),
                    blue(dbpath),
                    darkred(_("permission denied")),
                )
                self.output(
                    mytxt,
                    importance = 1,
                    type = "error",
                    header = red(" @@ ")
                )
            return False, mytxt

        def get_ts():
            ts = datetime.fromtimestamp(time.time())
            return "%s%s%s_%sh%sm%ss" % (ts.year, ts.month, ts.day, ts.hour,
                ts.minute, ts.second)

        comp_dbname = "%s%s.%s.bz2" % (etpConst['dbbackupprefix'], dbname, get_ts(),)
        comp_dbpath = os.path.join(backup_dir, comp_dbname)
        if not silent:
            mytxt = "%s: %s ..." % (
                darkgreen(_("Backing up database to")),
                blue(os.path.basename(comp_dbpath)),
            )
            self.output(
                mytxt,
                importance = 1,
                type = "info",
                header = blue(" @@ "),
                back = True
            )
        try:
            entropy.tools.compress_file(dbpath, comp_dbpath, bz2.BZ2File,
                compress_level)
        except:
            if not silent:
                entropy.tools.print_traceback()
            return False, _("Unable to compress")

        if not silent:
            mytxt = "%s: %s" % (
                darkgreen(_("Database backed up successfully")),
                blue(os.path.basename(comp_dbpath)),
            )
            self.output(
                mytxt,
                importance = 1,
                type = "info",
                header = blue(" @@ ")
            )
        return True, _("All fine")

    def restore_database(self, backup_path, db_destination, silent = False):

        bytes_required = 1024000*200
        db_dir = os.path.dirname(db_destination)
        if not (os.access(db_dir, os.W_OK) and os.path.isdir(db_dir) and \
            os.path.isfile(backup_path) and os.access(backup_path, os.R_OK) and \
            entropy.tools.check_required_space(db_dir, bytes_required)):

                if not silent:
                    mytxt = "%s: %s, %s" % (
                        darkred(_("Cannot restore selected backup")),
                        blue(os.path.basename(backup_path)),
                        darkred(_("permission denied")),
                    )
                    self.output(
                        mytxt,
                        importance = 1,
                        type = "error",
                        header = red(" @@ ")
                    )
                return False, mytxt

        if not silent:
            mytxt = "%s: %s => %s ..." % (
                darkgreen(_("Restoring backed up database")),
                blue(os.path.basename(backup_path)),
                blue(db_destination),
            )
            self.output(
                mytxt,
                importance = 1,
                type = "info",
                header = blue(" @@ "),
                back = True
            )

        import bz2
        try:
            entropy.tools.uncompress_file(backup_path, db_destination,
                bz2.BZ2File)
        except:
            if not silent:
                entropy.tools.print_traceback()
            return False, _("Unable to unpack")

        if not silent:
            mytxt = "%s: %s" % (
                darkgreen(_("Database restored successfully")),
                blue(os.path.basename(backup_path)),
            )
            self.output(
                mytxt,
                importance = 1,
                type = "info",
                header = blue(" @@ ")
            )
        self.clear_cache()
        return True, _("All fine")

    def list_backedup_client_databases(self, client_dbdir = None):
        if not client_dbdir:
            client_dbdir = os.path.dirname(etpConst['etpdatabaseclientfilepath'])
        return [os.path.join(client_dbdir, x) for x in os.listdir(client_dbdir) \
                    if x.startswith(etpConst['dbbackupprefix']) and \
                    os.access(os.path.join(client_dbdir, x), os.R_OK)
        ]

    def run_repositories_post_branch_switch_hooks(self, old_branch, new_branch):
        """
        This method is called whenever branch is successfully switched by user.
        Branch is switched when user wants to upgrade the OS to a new
        major release.
        Any repository can be shipped with a sh script which if available,
        handles system configuration to ease the migration.

        @param old_branch: previously set branch
        @type old_branch: string
        @param new_branch: newly set branch
        @type new_branch: string
        @return: tuple composed by (1) list of repositories whose script has
        been run and (2) bool describing if scripts exited with error
        @rtype: tuple(set, bool)
        """

        const_debug_write(__name__,
            "run_repositories_post_branch_switch_hooks: called")

        client_dbconn = self._installed_repository
        hooks_ran = set()
        if client_dbconn is None:
            const_debug_write(__name__,
                "run_repositories_post_branch_switch_hooks: clientdb not avail")
            return hooks_ran, True

        errors = False
        repo_data = self.SystemSettings['repositories']['available']
        repo_data_excl = self.SystemSettings['repositories']['available']
        all_repos = sorted(set(list(repo_data.keys()) + list(repo_data_excl.keys())))

        for repoid in all_repos:

            const_debug_write(__name__,
                "run_repositories_post_branch_switch_hooks: %s" % (
                    repoid,)
            )

            mydata = repo_data.get(repoid)
            if mydata is None:
                mydata = repo_data_excl.get(repoid)

            if mydata is None:
                const_debug_write(__name__,
                    "run_repositories_post_branch_switch_hooks: skipping %s" % (
                        repoid,)
                )
                continue

            branch_mig_script = mydata['post_branch_hop_script']
            branch_mig_md5sum = '0'
            if os.access(branch_mig_script, os.R_OK) and \
                os.path.isfile(branch_mig_script):
                branch_mig_md5sum = entropy.tools.md5sum(branch_mig_script)

            const_debug_write(__name__,
                "run_repositories_post_branch_switch_hooks: script md5: %s" % (
                    branch_mig_md5sum,)
            )

            # check if it is needed to run post branch migration script
            status_md5sums = client_dbconn.isBranchMigrationAvailable(
                repoid, old_branch, new_branch)
            if status_md5sums:
                if branch_mig_md5sum == status_md5sums[0]: # its stored md5
                    const_debug_write(__name__,
                        "run_repositories_post_branch_switch_hooks: skip %s" % (
                            branch_mig_script,)
                    )
                    continue # skipping, already ran the same script

            const_debug_write(__name__,
                "run_repositories_post_branch_switch_hooks: preparing run: %s" % (
                    branch_mig_script,)
                )

            if branch_mig_md5sum != '0':
                args = ["/bin/sh", branch_mig_script, repoid, 
                    etpConst['systemroot'] + "/", old_branch, new_branch]
                const_debug_write(__name__,
                    "run_repositories_post_branch_switch_hooks: run: %s" % (
                        args,)
                )
                proc = subprocess.Popen(args, stdin = sys.stdin,
                    stdout = sys.stdout, stderr = sys.stderr)
                # it is possible to ignore errors because
                # if it's a critical thing, upstream dev just have to fix
                # the script and will be automagically re-run
                br_rc = proc.wait()
                const_debug_write(__name__,
                    "run_repositories_post_branch_switch_hooks: rc: %s" % (
                        br_rc,)
                )
                if br_rc != 0:
                    errors = True

            const_debug_write(__name__,
                "run_repositories_post_branch_switch_hooks: done")

            # update metadata inside database
            # overriding post branch upgrade md5sum is INTENDED
            # here but NOT on the other function
            # this will cause the post-branch upgrade migration
            # script to be re-run also.
            client_dbconn.insertBranchMigration(repoid, old_branch, new_branch,
                branch_mig_md5sum, '0')

            const_debug_write(__name__,
                "run_repositories_post_branch_switch_hooks: db data: %s" % (
                    (repoid, old_branch, new_branch, branch_mig_md5sum, '0',),)
            )

            hooks_ran.add(repoid)

        return hooks_ran, errors

    def run_repository_post_branch_upgrade_hooks(self, pretend = False):
        """
        This method is called whenever branch is successfully switched by user
        and all the updates have been installed (also look at:
        run_repositories_post_branch_switch_hooks()).
        Any repository can be shipped with a sh script which if available,
        handles system configuration to ease the migration.

        @param pretend: do not run hooks but just return list of repos whose
            scripts should be run
        @type pretend: bool
        @return: tuple of length 2 composed by list of repositories whose
            scripts have been run and errors boolean)
        @rtype: tuple
        """

        const_debug_write(__name__,
            "run_repository_post_branch_upgrade_hooks: called"
        )

        client_dbconn = self._installed_repository
        hooks_ran = set()
        if client_dbconn is None:
            return hooks_ran, True

        repo_data = self.SystemSettings['repositories']['available']
        branch = self.SystemSettings['repositories']['branch']
        errors = False

        for repoid in self._enabled_repos:

            const_debug_write(__name__,
                "run_repository_post_branch_upgrade_hooks: repoid: %s" % (
                    (repoid,),
                )
            )

            mydata = repo_data.get(repoid)
            if mydata is None:
                const_debug_write(__name__,
                    "run_repository_post_branch_upgrade_hooks: repo data N/A")
                continue

            # check if branch upgrade script exists
            branch_upg_script = mydata['post_branch_upgrade_script']
            branch_upg_md5sum = '0'
            if os.access(branch_upg_script, os.R_OK) and \
                os.path.isfile(branch_upg_script):
                branch_upg_md5sum = entropy.tools.md5sum(branch_upg_script)

            if branch_upg_md5sum == '0':
                # script not found, skip completely
                const_debug_write(__name__,
                    "run_repository_post_branch_upgrade_hooks: %s: %s" % (
                        repoid, "branch upgrade script not avail",)
                )
                continue

            const_debug_write(__name__,
                "run_repository_post_branch_upgrade_hooks: script md5: %s" % (
                    branch_upg_md5sum,)
            )

            upgrade_data = client_dbconn.retrieveBranchMigration(branch)
            if upgrade_data.get(repoid) is None:
                # no data stored for this repository, skipping
                const_debug_write(__name__,
                    "run_repository_post_branch_upgrade_hooks: %s: %s" % (
                        repoid, "branch upgrade data not avail",)
                )
                continue
            repo_upgrade_data = upgrade_data[repoid]

            const_debug_write(__name__,
                "run_repository_post_branch_upgrade_hooks: upgrade data: %s" % (
                    repo_upgrade_data,)
            )

            for from_branch in sorted(repo_upgrade_data):

                const_debug_write(__name__,
                    "run_repository_post_branch_upgrade_hooks: upgrade: %s" % (
                        from_branch,)
                )

                # yeah, this is run for every branch even if script
                # which md5 is checked against is the same
                # this makes the code very flexible
                post_mig_md5, post_upg_md5 = repo_upgrade_data[from_branch]
                if branch_upg_md5sum == post_upg_md5:
                    # md5 is equal, this means that it's been already run
                    const_debug_write(__name__,
                        "run_repository_post_branch_upgrade_hooks: %s: %s" % (
                            "already run for from_branch", from_branch,)
                    )
                    continue

                hooks_ran.add(repoid)

                if pretend:
                    const_debug_write(__name__,
                        "run_repository_post_branch_upgrade_hooks: %s: %s => %s" % (
                            "pretend enabled, not actually running",
                            repoid, from_branch,
                        )
                    )
                    continue

                const_debug_write(__name__,
                    "run_repository_post_branch_upgrade_hooks: %s: %s" % (
                        "running upgrade script from_branch:", from_branch,)
                )

                args = ["/bin/sh", branch_upg_script, repoid,
                    etpConst['systemroot'] + "/", from_branch, branch]
                proc = subprocess.Popen(args, stdin = sys.stdin,
                    stdout = sys.stdout, stderr = sys.stderr)
                mig_rc = proc.wait()

                const_debug_write(__name__,
                    "run_repository_post_branch_upgrade_hooks: %s: %s" % (
                        "upgrade script exit status", mig_rc,)
                )

                if mig_rc != 0:
                    errors = True

                # save branch_upg_md5sum in db
                client_dbconn.setBranchMigrationPostUpgradeMd5sum(repoid,
                    from_branch, branch, branch_upg_md5sum)

                const_debug_write(__name__,
                    "run_repository_post_branch_upgrade_hooks: %s: %s" % (
                        "saved upgrade data",
                        (repoid, from_branch, branch, branch_upg_md5sum,),
                    )
                )

        return hooks_ran, errors


class MiscMixin:

    # resources lock file object container
    RESOURCES_LOCK_F_REF = None
    RESOURCES_LOCK_F_COUNT = 0

    def reload_constants(self):
        initconfig_entropy_constants(etpSys['rootdir'])
        self.SystemSettings.clear()

    def setup_default_file_perms(self, filepath):
        # setup file permissions
        const_setup_file(filepath, etpConst['entropygid'], 0o664)

    def resources_create_lock(self):
        acquired = self.create_pid_file_lock(
            etpConst['locks']['using_resources'])
        if acquired:
            MiscMixin.RESOURCES_LOCK_F_COUNT += 1
        return acquired

    def resources_remove_lock(self):

        # decrement lock counter
        if MiscMixin.RESOURCES_LOCK_F_COUNT > 0:
            MiscMixin.RESOURCES_LOCK_F_COUNT -= 1

        # if lock counter > 0, still locked
        # waiting for other upper-level calls
        if MiscMixin.RESOURCES_LOCK_F_COUNT > 0:
            return

        f_obj = MiscMixin.RESOURCES_LOCK_F_REF
        if f_obj is not None:
            fcntl.flock(f_obj.fileno(), fcntl.LOCK_UN)

            if f_obj is not None:
                f_obj.close()
            MiscMixin.RESOURCES_LOCK_F_REF = None

        if os.path.isfile(etpConst['locks']['using_resources']):
            os.remove(etpConst['locks']['using_resources'])

    def resources_check_lock(self):
        return self.check_pid_file_lock(etpConst['locks']['using_resources'])

    def check_pid_file_lock(self, pidfile):
        if not os.path.isfile(pidfile):
            return False # not locked
        f = open(pidfile)
        s_pid = f.readline().strip()
        f.close()
        try:
            s_pid = int(s_pid)
        except ValueError:
            return False # not locked
        # is it our pid?

        mypid = os.getpid()
        if (s_pid != mypid) and const_pid_exists(s_pid):
            # is it running
            return True # locked
        return False

    def create_pid_file_lock(self, pidfile, mypid = None):

        if MiscMixin.RESOURCES_LOCK_F_REF is not None:
            # already locked, reentrant lock
            return True

        lockdir = os.path.dirname(pidfile)
        if not os.path.isdir(lockdir):
            os.makedirs(lockdir, 0o775)
        const_setup_perms(lockdir, etpConst['entropygid'])
        if mypid == None:
            mypid = os.getpid()

        pid_f = open(pidfile, "w")
        try:
            fcntl.flock(pid_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError as err:
            if err.errno not in (errno.EACCES, errno.EAGAIN,):
                # ouch, wtf?
                raise
            pid_f.close()
            return False # lock already acquired

        pid_f.write(str(mypid))
        pid_f.flush()
        MiscMixin.RESOURCES_LOCK_F_REF = pid_f
        return True

    def application_lock_check(self, silent = False):
        # check if another instance is running
        etpConst['applicationlock'] = False
        const_setup_entropy_pid(just_read = True)

        locked = etpConst['applicationlock']
        if locked:
            if not silent:
                self.output(
                    red(_("Another Entropy instance is currently active, cannot satisfy your request.")),
                    importance = 1,
                    type = "error",
                    header = darkred(" @@ ")
                )
            return True
        return False

    def lock_check(self, check_function):

        lock_count = 0
        max_lock_count = 600
        sleep_seconds = 0.5

        # check lock file
        while True:
            locked = check_function()
            if not locked:
                if lock_count > 0:
                    self.output(
                        blue(_("Resources unlocked, let's go!")),
                        importance = 1,
                        type = "info",
                        header = darkred(" @@ ")
                    )
                    # wait for other process to exit
                    # 5 seconds should be enough
                    time.sleep(5)
                break
            if lock_count >= max_lock_count:
                mycalc = max_lock_count*sleep_seconds/60
                self.output(
                    blue(_("Resources still locked after %s minutes, giving up!")) % (mycalc,),
                    importance = 1,
                    type = "warning",
                    header = darkred(" @@ ")
                )
                return True # gave up
            lock_count += 1
            self.output(
                blue(_("Resources locked, sleeping %s seconds, check #%s/%s")) % (
                        sleep_seconds,
                        lock_count,
                        max_lock_count,
                ),
                importance = 1,
                type = "warning",
                header = darkred(" @@ "),
                back = True
            )
            time.sleep(sleep_seconds)
        return False # yay!

    def backup_constant(self, constant_name):
        if constant_name in etpConst:
            myinst = etpConst[constant_name]
            if type(etpConst[constant_name]) in (list, tuple):
                myinst = etpConst[constant_name][:]
            elif type(etpConst[constant_name]) in (dict, set):
                myinst = etpConst[constant_name].copy()
            else:
                myinst = etpConst[constant_name]
            etpConst['backed_up'].update({constant_name: myinst})
        else:
            t = _("Nothing to backup in etpConst with %s key") % (constant_name,)
            raise AttributeError(t)

    def set_priority(self, low = 0):
        return const_set_nice_level(low)

    def reload_repositories_config(self, repositories = None):
        if repositories is None:
            repositories = self._enabled_repos
        for repoid in repositories:
            self.open_repository(repoid)

    def switch_chroot(self, chroot = ""):
        # clean caches
        self.clear_cache()
        self.close_all_repositories()
        if chroot.endswith("/"):
            chroot = chroot[:-1]
        etpSys['rootdir'] = chroot
        self.reload_constants()
        self.validate_repositories()
        self.reopen_installed_repository()
        # keep them closed, since SystemSettings.clear() is called
        # above on reopen_installed_repository()
        self.close_all_repositories()
        if chroot:
            try:
                self._installed_repository.resetTreeupdatesDigests()
            except:
                pass

    def is_installed_idpackage_in_system_mask(self, idpackage):
        client_plugin_id = etpConst['system_settings_plugins_ids']['client_plugin']
        mask_installed = self.SystemSettings[client_plugin_id]['system_mask']['repos_installed']
        if idpackage in mask_installed:
            return True
        return False

    def unused_packages_test(self, dbconn = None):
        if dbconn == None: dbconn = self._installed_repository
        return [x for x in dbconn.retrieveUnusedIdpackages() if self.validate_package_removal(x)]

    def is_entropy_package_free(self, pkg_id, repo_id):
        """
        Return whether given Entropy package match tuple points to a free
        (as in freedom) package.
        """
        cl_id = self.sys_settings_client_plugin_id
        repo_sys_data = self.SystemSettings[cl_id]['repositories']

        dbconn = self.open_repository(repo_id)

        wl = repo_sys_data['license_whitelist'].get(repo_id)
        if not wl: # no whitelist available
            return True

        keys = dbconn.retrieveLicensedataKeys(pkg_id)
        keys = [x for x in keys if x not in wl]
        if keys:
            return False
        return True

    def get_licenses_to_accept(self, install_queue):

        cl_id = self.sys_settings_client_plugin_id
        repo_sys_data = self.SystemSettings[cl_id]['repositories']
        lic_accepted = self.SystemSettings['license_accept']

        licenses = {}
        for match in install_queue:
            repoid = match[1]
            dbconn = self.open_repository(repoid)
            wl = repo_sys_data['license_whitelist'].get(repoid)
            if not wl:
                continue
            keys = dbconn.retrieveLicensedataKeys(match[0])
            keys = [x for x in keys if x not in lic_accepted]
            for key in keys:
                if key in wl:
                    continue
                found = self._installed_repository.isLicenseAccepted(key)
                if found:
                    continue
                obj = licenses.setdefault(key, set())
                obj.add(match)

        return licenses

    def get_text_license(self, license_name, repoid):
        dbconn = self.open_repository(repoid)
        text = dbconn.retrieveLicenseText(license_name)
        tempfile = entropy.tools.get_random_temp_file()
        f = open(tempfile, "w")
        f.write(text)
        f.flush()
        f.close()
        return tempfile

    def set_branch(self, branch):
        """
        Set new Entropy branch. This is NOT thread-safe.
        Please note that if you call this method all your
        repository instance references will become invalid.
        This is caused by close_all_repositories and SystemSettings
        clear methods.
        Once you changed branch, the repository databases won't be
        available until you fetch them (through Repositories class)

        @param branch -- new branch
        @type branch basestring
        @return None
        """
        self.Cacher.discard()
        self.Cacher.stop()
        self.clear_cache()
        self.close_all_repositories()
        # etpConst should be readonly but we override the rule here
        # this is also useful when no config file or parameter into it exists
        etpConst['branch'] = branch
        entropy.tools.write_parameter_to_file(etpConst['repositoriesconf'],
            "branch", branch)
        # there are no valid repos atm
        del self._enabled_repos[:]
        self.SystemSettings.clear()

        # reset treeupdatesactions
        self.reopen_installed_repository()
        self._installed_repository.resetTreeupdatesDigests()
        self.validate_repositories(quiet = True)
        self.close_all_repositories()
        if self.xcache:
            self.Cacher.start()

    def get_meant_packages(self, search_term, from_installed = False,
        valid_repos = None):

        if valid_repos is None:
            valid_repos = []

        pkg_data = []
        atom_srch = False
        if "/" in search_term:
            atom_srch = True

        if from_installed:
            if hasattr(self, '_installed_repository'):
                if self._installed_repository is not None:
                    valid_repos.append(self._installed_repository)

        elif not valid_repos:
            valid_repos.extend(self._enabled_repos[:])

        for repo in valid_repos:
            if const_isstring(repo):
                dbconn = self.open_repository(repo)
            elif isinstance(repo, EntropyRepository):
                dbconn = repo
            else:
                continue
            pkg_data.extend([(x, repo,) for x in \
                dbconn.searchSimilarPackages(search_term, atom = atom_srch)])

        return pkg_data

    def get_package_groups(self):
        """
        Return Entropy Package Groups metadata. The returned dictionary
        contains information to make Entropy Client users to group packages
        into "macro" categories.

        @return: Entropy Package Groups metadata
        @rtype: dict
        """
        from entropy.spm.plugins.factory import get_default_class
        spm = get_default_class()
        groups = spm.get_package_groups().copy()

        # expand metadata
        categories = self.get_package_categories()
        for data in list(groups.values()):

            exp_cats = set()
            for g_cat in data['categories']:
                exp_cats.update([x for x in categories if x.startswith(g_cat)])
            data['categories'] = sorted(exp_cats)

        return groups

    def get_package_categories(self):
        categories = set()
        for repo in self._enabled_repos:
            dbconn = self.open_repository(repo)
            catsdata = dbconn.listAllCategories()
            categories.update(set([x[1] for x in catsdata]))
        return sorted(categories)

    def get_category_description(self, category):

        data = {}
        for repo in self._enabled_repos:
            try:
                dbconn = self.open_repository(repo)
            except RepositoryError:
                continue
            try:
                data = dbconn.retrieveCategoryDescription(category)
            except (OperationalError, IntegrityError,):
                continue
            if data:
                break

        return data

    def list_installed_packages_in_category(self, category):
        pkg_matches = set([x[1] for x in \
            self._installed_repository.searchCategory(category)])
        return pkg_matches

    def get_package_match_config_protect(self, match, mask = False):

        idpackage, repoid = match
        dbconn = self.open_repository(repoid)
        cl_id = self.sys_settings_client_plugin_id
        misc_data = self.SystemSettings[cl_id]['misc']
        if mask:
            config_protect = set(dbconn.retrieveProtectMask(idpackage).split())
            config_protect |= set(misc_data['configprotectmask'])
        else:
            config_protect = set(dbconn.retrieveProtect(idpackage).split())
            config_protect |= set(misc_data['configprotect'])
        config_protect = [etpConst['systemroot']+x for x in config_protect]

        return sorted(config_protect)

    def get_installed_package_config_protect(self, idpackage, mask = False):

        if self._installed_repository == None:
            return []
        cl_id = self.sys_settings_client_plugin_id
        misc_data = self.SystemSettings[cl_id]['misc']
        if mask:
            _pmask = self._installed_repository.retrieveProtectMask(idpackage).split()
            config_protect = set(_pmask)
            config_protect |= set(misc_data['configprotectmask'])
        else:
            _protect = self._installed_repository.retrieveProtect(idpackage).split()
            config_protect = set(_protect)
            config_protect |= set(misc_data['configprotect'])
        config_protect = [etpConst['systemroot']+x for x in config_protect]

        return sorted(config_protect)

    def get_system_config_protect(self, mask = False):

        if self._installed_repository == None:
            return []

        # FIXME: workaround because this method is called
        # before misc_parser
        cl_id = self.sys_settings_client_plugin_id
        misc_data = self.SystemSettings[cl_id]['misc']
        if mask:
            _pmask = self._installed_repository.listConfigProtectEntries(mask = True)
            config_protect = set(_pmask)
            config_protect |= set(misc_data['configprotectmask'])
        else:
            _protect = self._installed_repository.listConfigProtectEntries()
            config_protect = set(_protect)
            config_protect |= set(misc_data['configprotect'])
        config_protect = [etpConst['systemroot']+x for x in config_protect]

        return sorted(config_protect)

    def inject_entropy_database_into_package(self, package_filename, data,
        treeupdates_actions = None):
        tmp_fd, tmp_path = tempfile.mkstemp()
        os.close(tmp_fd)
        dbconn = self.open_generic_database(tmp_path)
        dbconn.initializeDatabase()
        dbconn.addPackage(data, revision = data['revision'])
        if treeupdates_actions != None:
            dbconn.bumpTreeUpdatesActions(treeupdates_actions)
        dbconn.commitChanges()
        dbconn.closeDB()
        entropy.tools.aggregate_entropy_metadata(package_filename, tmp_path)
        os.remove(tmp_path)

    def quickpkg(self, atomstring, savedir = None):
        if savedir == None:
            savedir = etpConst['packagestmpdir']
            if not os.path.isdir(etpConst['packagestmpdir']):
                os.makedirs(etpConst['packagestmpdir'])
        # match package
        match = self._installed_repository.atomMatch(atomstring)
        if match[0] == -1:
            return -1, None, None
        atom = self._installed_repository.atomMatch(match[0])
        pkgdata = self._installed_repository.getPackageData(match[0])
        resultfile = self.quickpkg_handler(pkgdata = pkgdata, dirpath = savedir)
        if resultfile == None:
            return -1, atom, None
        else:
            return 0, atom, resultfile

    def quickpkg_handler(self, pkgdata, dirpath, edb = True,
           fake = False, compression = "bz2", shiftpath = ""):

        import tarfile

        if compression not in ("bz2", "", "gz"):
            compression = "bz2"

        # getting package info
        pkgtag = ''
        pkgrev = "~"+str(pkgdata['revision'])
        if pkgdata['versiontag']:
            pkgtag = "#"+pkgdata['versiontag']
        # + version + tag
        pkgname = pkgdata['name']+"-"+pkgdata['version']+pkgrev+pkgtag
        pkgcat = pkgdata['category']
        pkg_path = dirpath+os.path.sep+pkgname+etpConst['packagesext']
        if os.path.isfile(pkg_path):
            os.remove(pkg_path)
        tar = tarfile.open(pkg_path, "w:"+compression)

        if not fake:

            contents = sorted([x for x in pkgdata['content']])

            # collect files
            for path in contents:
                # convert back to filesystem str
                encoded_path = path
                path = path.encode('raw_unicode_escape')
                path = shiftpath+path
                try:
                    exist = os.lstat(path)
                except OSError:
                    continue # skip file
                arcname = path[len(shiftpath):] # remove shiftpath
                if arcname.startswith("/"):
                    arcname = arcname[1:] # remove trailing /
                ftype = pkgdata['content'][encoded_path]
                if str(ftype) == '0':
                    # force match below, '0' means databases without ftype
                    ftype = 'dir'
                if 'dir' == ftype and \
                    not stat.S_ISDIR(exist.st_mode) and \
                    os.path.isdir(path):
                    # workaround for directory symlink issues
                    path = os.path.realpath(path)

                tarinfo = tar.gettarinfo(path, arcname)

                if stat.S_ISREG(exist.st_mode):
                    tarinfo.mode = stat.S_IMODE(exist.st_mode)
                    tarinfo.type = tarfile.REGTYPE
                    f = open(path)
                    try:
                        tar.addfile(tarinfo, f)
                    finally:
                        f.close()
                else:
                    tar.addfile(tarinfo)

        tar.close()

        # append SPM metadata
        Spm = self.Spm()
        Spm.append_metadata_to_package(pkgcat + "/" + pkgname, pkg_path)
        if edb:
            self.inject_entropy_database_into_package(pkg_path, pkgdata)

        if os.path.isfile(pkg_path):
            return pkg_path
        return None


class MatchMixin:

    def get_package_action(self, match):
        """
            @input: matched atom (idpackage,repoid)
            @output:
                    upgrade: int(2)
                    install: int(1)
                    reinstall: int(0)
                    downgrade: int(-1)
        """
        dbconn = self.open_repository(match[1])
        pkgkey, pkgslot = dbconn.retrieveKeySlot(match[0])
        results = self._installed_repository.searchKeySlot(pkgkey, pkgslot)
        if not results:
            return 1

        installed_idpackage = results[0][0]
        pkgver, pkgtag, pkgrev = dbconn.getVersioningData(match[0])
        installed_ver, installed_tag, installed_rev = \
            self._installed_repository.getVersioningData(installed_idpackage)
        pkgcmp = entropy.tools.entropy_compare_versions(
            (pkgver, pkgtag, pkgrev),
            (installed_ver, installed_tag, installed_rev))
        if pkgcmp == 0:
            # check digest, if it differs, we should mark pkg as update
            # we don't want users to think that they are "reinstalling" stuff
            # because it will just confuse them
            inst_digest = self._installed_repository.retrieveDigest(installed_idpackage)
            repo_digest = dbconn.retrieveDigest(match[0])
            if inst_digest != repo_digest:
                return 2
            return 0
        elif pkgcmp > 0:
            return 2
        return -1

    def get_masked_package_reason(self, match):
        idpackage, repoid = match
        dbconn = self.open_repository(repoid)
        idpackage, idreason = dbconn.idpackageValidator(idpackage)
        masked = False
        if idpackage == -1:
            masked = True
        return masked, idreason, self.SystemSettings['pkg_masking_reasons'].get(idreason)

    def get_match_conflicts(self, match):
        m_id, m_repo = match
        dbconn = self.open_repository(m_repo)
        conflicts = dbconn.retrieveConflicts(m_id)
        found_conflicts = set()
        for conflict in conflicts:
            my_m_id, my_m_rc = self._installed_repository.atomMatch(conflict)
            if my_m_id != -1:
                # check if the package shares the same slot
                match_data = dbconn.retrieveKeySlot(m_id)
                installed_match_data = self._installed_repository.retrieveKeySlot(my_m_id)
                if match_data != installed_match_data:
                    found_conflicts.add(my_m_id)
        return found_conflicts

    def is_match_masked(self, match, live_check = True):
        m_id, m_repo = match
        dbconn = self.open_repository(m_repo)
        idpackage, idreason = dbconn.idpackageValidator(m_id, live = live_check)
        if idpackage != -1:
            return False
        return True

    def is_match_masked_by_user(self, match, live_check = True):
        # (query_status,masked?,)
        m_id, m_repo = match
        if m_repo not in self._enabled_repos: return False
        dbconn = self.open_repository(m_repo)
        idpackage, idreason = dbconn.idpackageValidator(m_id, live = live_check)
        if idpackage != -1: return False #,False
        myr = self.SystemSettings['pkg_masking_reference']
        user_masks = [myr['user_package_mask'], myr['user_license_mask'],
            myr['user_live_mask']]
        if idreason in user_masks:
            return True #,True
        return False #,True

    def is_match_unmasked_by_user(self, match, live_check = True):
        # (query_status,unmasked?,)
        m_id, m_repo = match
        if m_repo not in self._enabled_repos: return False
        dbconn = self.open_repository(m_repo)
        idpackage, idreason = dbconn.idpackageValidator(m_id, live = live_check)
        if idpackage == -1: return False #,False
        myr = self.SystemSettings['pkg_masking_reference']
        user_masks = [
            myr['user_package_unmask'], myr['user_live_unmask'],
            myr['user_package_keywords'], myr['user_repo_package_keywords_all'],
            myr['user_repo_package_keywords']
        ]
        if idreason in user_masks:
            return True #,True
        return False #,True

    def mask_match(self, match, method = 'atom', dry_run = False):
        if self.is_match_masked(match, live_check = False):
            return True
        methods = {
            'atom': self.mask_match_by_atom,
            'keyslot': self.mask_match_by_keyslot,
        }
        rc = self._mask_unmask_match(match, method, methods, dry_run = dry_run)
        if dry_run: # inject if done "live"
            self.SystemSettings['live_packagemasking']['unmask_matches'].discard(match)
            self.SystemSettings['live_packagemasking']['mask_matches'].add(match)
        return rc

    def unmask_match(self, match, method = 'atom', dry_run = False):
        if not self.is_match_masked(match, live_check = False):
            return True
        methods = {
            'atom': self.unmask_match_by_atom,
            'keyslot': self.unmask_match_by_keyslot,
        }
        rc = self._mask_unmask_match(match, method, methods, dry_run = dry_run)
        if dry_run: # inject if done "live"
            self.SystemSettings['live_packagemasking']['unmask_matches'].add(match)
            self.SystemSettings['live_packagemasking']['mask_matches'].discard(match)
        return rc

    def _mask_unmask_match(self, match, method, methods_reference,
        dry_run = False):

        f = methods_reference.get(method)
        if not hasattr(f, '__call__'):
            raise AttributeError('%s: %s' % (
                _("not a valid method"), method,) )

        self.Cacher.discard()
        self.SystemSettings._clear_repository_cache(match[1])
        done = f(match, dry_run)
        if done and not dry_run:
            self.SystemSettings.clear()

        cl_id = self.sys_settings_client_plugin_id
        self.SystemSettings[cl_id]['masking_validation']['cache'].clear()
        return done

    def unmask_match_by_atom(self, match, dry_run = False):
        m_id, m_repo = match
        dbconn = self.open_repository(m_repo)
        atom = dbconn.retrieveAtom(m_id)
        return self.unmask_match_generic(match, atom, dry_run = dry_run)

    def unmask_match_by_keyslot(self, match, dry_run = False):
        m_id, m_repo = match
        dbconn = self.open_repository(m_repo)
        key, slot = dbconn.retrieveKeySlot(m_id)
        keyslot = "%s%s%s" % (key, etpConst['entropyslotprefix'], slot,)
        return self.unmask_match_generic(match, keyslot, dry_run = dry_run)

    def mask_match_by_atom(self, match, dry_run = False):
        m_id, m_repo = match
        dbconn = self.open_repository(m_repo)
        atom = dbconn.retrieveAtom(m_id)
        return self.mask_match_generic(match, atom, dry_run = dry_run)

    def mask_match_by_keyslot(self, match, dry_run = False):
        m_id, m_repo = match
        dbconn = self.open_repository(m_repo)
        key, slot = dbconn.retrieveKeySlot(m_id)
        keyslot = "%s%s%s" % (key, etpConst['entropyslotprefix'], slot)
        return self.mask_match_generic(match, keyslot, dry_run = dry_run)

    def unmask_match_generic(self, match, keyword, dry_run = False):
        self.clear_match_mask(match, dry_run)
        m_file = self.SystemSettings.get_setting_files_data()['unmask']
        return self._mask_unmask_match_generic(keyword, m_file,
            dry_run = dry_run)

    def mask_match_generic(self, match, keyword, dry_run = False):
        self.clear_match_mask(match, dry_run)
        m_file = self.SystemSettings.get_setting_files_data()['mask']
        return self._mask_unmask_match_generic(keyword, m_file,
            dry_run = dry_run)

    def _mask_unmask_match_generic(self, keyword, m_file, dry_run = False):
        exist = False
        if not os.path.isfile(m_file):
            if not os.access(os.path.dirname(m_file), os.W_OK):
                return False # cannot write
        elif not os.access(m_file, os.W_OK):
            return False
        elif not dry_run:
            exist = True

        if dry_run:
            return True

        content = []
        if exist:
            f = open(m_file, "r")
            content = [x.strip() for x in f.readlines()]
            f.close()
        content.append(keyword)
        m_file_tmp = m_file+".tmp"
        f = open(m_file_tmp, "w")
        for line in content:
            f.write(line+"\n")
        f.flush()
        f.close()
        shutil.move(m_file_tmp, m_file)
        return True

    def clear_match_mask(self, match, dry_run = False):
        setting_data = self.SystemSettings.get_setting_files_data()
        masking_list = [setting_data['mask'], setting_data['unmask']]
        return self._clear_match_generic(match, masking_list = masking_list,
            dry_run = dry_run)

    def _clear_match_generic(self, match, masking_list = None, dry_run = False):

        if dry_run:
            return

        if masking_list is None:
            masking_list = []

        self.SystemSettings['live_packagemasking']['unmask_matches'].discard(
            match)
        self.SystemSettings['live_packagemasking']['mask_matches'].discard(
            match)

        new_mask_list = [x for x in masking_list if os.path.isfile(x) \
            and os.access(x, os.W_OK)]

        for mask_file in new_mask_list:

            tmp_fd, tmp_path = tempfile.mkstemp()
            os.close(tmp_fd)

            with open(mask_file, "r") as mask_f:
                with open(tmp_path, "w") as tmp_f:
                    for line in mask_f.readlines():
                        strip_line = line.strip()

                        if not (strip_line.startswith("#") or not strip_line):
                            mymatch = self.atom_match(strip_line,
                                packagesFilter = False)
                            if mymatch == match:
                                continue

                        tmp_f.write(line)

            try:
                os.rename(tmp_path, mask_file)
            except OSError:
                shutil.copy2(tmp_path, mask_file)
                os.remove(tmp_path)

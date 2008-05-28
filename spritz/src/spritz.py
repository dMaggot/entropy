#!/usr/bin/python -tt
# -*- coding: iso-8859-1 -*-
#    Yum Exteder (yumex) - A GUI for yum
#    Copyright (C) 2006 Tim Lauridsen < tim<AT>yum-extender<DOT>org > 
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.

# Base Python Imports
import sys, os, pty
import logging
import traceback
import commands
import time

# Entropy Imports
sys.path.insert(0,"../../libraries")
sys.path.insert(1,"../../client")
sys.path.insert(2,"/usr/lib/entropy/libraries")
sys.path.insert(3,"/usr/lib/entropy/client")
from entropyConstants import *
import exceptionTools
from packages import EntropyPackages
from entropyapi import EquoConnection, QueueExecutor
from entropy import ErrorReportInterface
from entropy_i18n import _

# GTK Imports
import gtk, gobject
import thread
import exceptions
from etpgui.widgets import UI, Controller
from etpgui import *
from spritz_setup import fakeoutfile, fakeinfile, cleanMarkupString

# spritz imports
import filters
from gui import SpritzGUI
from dialogs import *


class SpritzController(Controller):
    ''' This class contains all glade signal callbacks '''


    def __init__( self ):
        self.etpbase = EntropyPackages(EquoConnection)
        # Create and ui object contains the widgets.
        ui = UI( const.GLADE_FILE , 'main', 'entropy' )
        addrepo_ui = UI( const.GLADE_FILE , 'addRepoWin', 'entropy' )

        advinfo_ui = UI( const.GLADE_FILE , 'advInfo', 'entropy' )
        wait_ui = UI( const.GLADE_FILE , 'waitWindow', 'entropy' )
        # init the Controller Class to connect signals.
        Controller.__init__( self, ui, addrepo_ui, wait_ui, advinfo_ui )

        self.clipboard = gtk.Clipboard()
        self.pty = pty.openpty()
        self.output = fakeoutfile(self.pty[1])
        self.input = fakeinfile(self.pty[1])
        if "--debug" not in sys.argv:
            sys.stdout = self.output
            sys.stderr = self.output
            sys.stdin = self.input


    def quit(self, widget=None, event=None ):
        ''' Main destroy Handler '''
        gtkEventThread.doQuit()
        if self.isWorking:
            self.quitNow = True
            self.logger.critical(_('Quiting, please wait !!!!!!'))
            time.sleep(3)
            self.exitNow()
            return False
        else:
            self.exitNow()
            return False

    def exitNow(self):
        try:
            gtk.main_quit()       # Exit gtk
        except RuntimeError,e:
            pass
        sys.exit( 1 )         # Terminate Program

    def __getSelectedRepoIndex( self ):
        selection = self.repoView.view.get_selection()
        repodata = selection.get_selected()
        # get text
        if repodata[1] != None:
            repoid = self.repoView.get_repoid(repodata)
            # do it if it's enabled
            if repoid in etpRepositoriesOrder:
                idx = etpRepositoriesOrder.index(repoid)
                return idx, repoid, repodata
        return None, None, None

    def runEditor(self, filename, delete = False):
        cmd = ' '.join([self.fileEditor,filename])
        task = self.Equo.entropyTools.parallelTask(self.__runEditor, {'cmd': cmd, 'delete': delete,'file': filename})
        task.start()

    def __runEditor(self, data):
        os.system(data['cmd']+"&> /dev/null")
        delete = data['delete']
        if delete and os.path.isfile(data['file']):
            try:
                os.remove(data['file'])
            except OSError:
                pass

    def __get_Edit_filename(self):
        selection = self.filesView.view.get_selection()
        model, iterator = selection.get_selected()
        if model != None and iterator != None:
            identifier = model.get_value( iterator, 0 )
            destination = model.get_value( iterator, 2 )
            source = model.get_value( iterator, 1 )
            source = os.path.join(os.path.dirname(destination),source)
            return identifier, source, destination
        return 0,None,None

    def on_updateAdvSelected_clicked( self, widget ):

        if not self.etpbase.selected_advisory_item:
            return

        key, affected, data = self.etpbase.selected_advisory_item
        if not affected:
            okDialog( self.ui.main, _("The chosen package is not vulnerable") )
            return
        atoms = set()
        for mykey in data['affected']:
            affected_data = data['affected'][mykey][0]
            atoms |= set(affected_data['unaff_atoms'])

        self.add_atoms_to_queue(atoms)
        if rc: okDialog( self.ui.main, _("Packages in Advisory have been queued.") )

    def on_updateAdvAll_clicked( self, widget ):

        adv_data = self.Advisories.get_advisories_metadata()
        atoms = set()
        for key in adv_data:
            affected = self.Advisories.is_affected(key)
            if not affected:
                continue
            for mykey in adv_data[key]['affected']:
                affected_data = adv_data[key]['affected'][mykey][0]
                atoms |= set(affected_data['unaff_atoms'])

        rc = self.add_atoms_to_queue(atoms)
        if rc: okDialog( self.ui.main, _("Packages in all Advisories have been queued.") )

    def add_atoms_to_queue(self, atoms):

        self.setBusy()
        # resolve atoms
        matches = set()
        for atom in atoms:
            match = self.Equo.atomMatch(atom)
            if match[0] != -1:
                matches.add(match)
        if not matches:
            okDialog( self.ui.main, _("Packages not found in repositories, try again later.") )
            self.unsetBusy()
            return

        resolved = []
        packages = self.etpbase.getPackages('updates') + \
                    self.etpbase.getPackages('available') + \
                    self.etpbase.getPackages('reinstallable')
        for match in matches:
            resolved.append(self.etpbase.getPackageItem(match,True)[0])

        for obj in resolved:
            if obj in self.queue.packages['i'] + \
                        self.queue.packages['u'] + \
                        self.queue.packages['r'] + \
                        self.queue.packages['rr']:
                continue
            oldqueued = obj.queued
            obj.queued = 'u'
            status, myaction = self.queue.add(obj)
            if status != 0:
                obj.queued = oldqueued
            self.queueView.refresh()
            self.ui.viewPkg.queue_draw()

        self.unsetBusy()
        return True


    def on_advInfoButton_clicked( self, widget ):
        if self.etpbase.selected_advisory_item:
            self.loadAdvInfoMenu(self.etpbase.selected_advisory_item)

    def on_pkgInfoButton_clicked( self, widget ):
        if self.etpbase.selected_treeview_item:
            self.loadPkgInfoMenu(self.etpbase.selected_treeview_item)

    def on_filesDelete_clicked( self, widget ):
        identifier, source, dest = self.__get_Edit_filename()
        if not identifier:
            return True
        self.Equo.FileUpdates.remove_file(identifier)
        self.filesView.populate(self.Equo.FileUpdates.scandata)

    def on_filesMerge_clicked( self, widget ):
        identifier, source, dest = self.__get_Edit_filename()
        if not identifier:
            return True
        self.Equo.FileUpdates.merge_file(identifier)
        self.filesView.populate(self.Equo.FileUpdates.scandata)

    def on_mergeFiles_clicked( self, widget ):
        self.Equo.FileUpdates.scanfs(dcache = True)
        keys = self.Equo.FileUpdates.scandata.keys()
        for key in keys:
            self.Equo.FileUpdates.merge_file(key)
            # it's cool watching it runtime
            self.filesView.populate(self.Equo.FileUpdates.scandata)

    def on_deleteFiles_clicked( self, widget ):
        self.Equo.FileUpdates.scanfs(dcache = True)
        keys = self.Equo.FileUpdates.scandata.keys()
        for key in keys:
            self.Equo.FileUpdates.remove_file(key)
            # it's cool watching it runtime
            self.filesView.populate(self.Equo.FileUpdates.scandata)

    def on_filesEdit_clicked( self, widget ):
        identifier, source, dest = self.__get_Edit_filename()
        if not identifier:
            return True
        self.runEditor(source)

    def on_filesView_row_activated( self, widget, iterator, path ):
        self.on_filesViewChanges_clicked(widget)

    def on_filesViewChanges_clicked( self, widget ):
        identifier, source, dest = self.__get_Edit_filename()
        if not identifier:
            return True
        randomfile = self.Equo.entropyTools.getRandomTempFile()+".diff"
        diffcmd = "diff -Nu "+dest+" "+source+" > "+randomfile
        os.system(diffcmd)
        self.runEditor(randomfile, delete = True)

    def on_switchBranch_clicked( self, widget ):
        branch = inputBox(self.ui.main, _("Branch switching"), _("Enter a valid branch you want to switch to")+"     ", input_text = etpConst['branch'])
        branches = self.Equo.listAllAvailableBranches()
        if branch not in branches:
            okDialog( self.ui.main, _("The selected branch is not available.") )
        else:
            self.Equo.move_to_branch(branch)
            msg = "%s %s. %s." % (_("New branch is"),etpConst['branch'],_("It is suggested to synchronize repositories"),)
            okDialog( self.ui.main, msg )

    def on_shiftUp_clicked( self, widget ):
        idx, repoid, iterdata = self.__getSelectedRepoIndex()
        if idx != None:
            path = iterdata[0].get_path(iterdata[1])[0]
            if path > 0 and idx > 0:
                idx -= 1
                self.Equo.shiftRepository(repoid, idx)
                # get next iter
                prev = iterdata[0].get_iter(path-1)
                self.repoView.store.swap(iterdata[1],prev)

    def on_shiftDown_clicked( self, widget ):
        idx, repoid, iterdata = self.__getSelectedRepoIndex()
        if idx != None:
            next = iterdata[0].iter_next(iterdata[1])
            if next:
                idx += 1
                self.Equo.shiftRepository(repoid, idx)
                self.repoView.store.swap(iterdata[1],next)

    def on_mirrorDown_clicked( self, widget ):
        selection = self.repoMirrorsView.view.get_selection()
        urldata = selection.get_selected()
        # get text
        if urldata[1] != None:
            next = urldata[0].iter_next(urldata[1])
            if next:
                self.repoMirrorsView.store.swap(urldata[1],next)

    def on_mirrorUp_clicked( self, widget ):
        selection = self.repoMirrorsView.view.get_selection()
        urldata = selection.get_selected()
        # get text
        if urldata[1] != None:
            path = urldata[0].get_path(urldata[1])[0]
            if path > 0:
                # get next iter
                prev = urldata[0].get_iter(path-1)
                self.repoMirrorsView.store.swap(urldata[1],prev)

    def on_repoMirrorAdd_clicked( self, widget ):
        text = inputBox(self.addrepo_ui.addRepoWin, _("Insert URL"), _("Enter a download mirror, HTTP or FTP")+"   ")
        # call liststore and tell to add
        if text:
            # validate url
            if not (text.startswith("http://") or text.startswith("ftp://") or text.startswith("file://")):
                okDialog( self.addrepo_ui.addRepoWin, _("You must enter either a HTTP or a FTP url.") )
            else:
                self.repoMirrorsView.add(text)

    def on_repoMirrorRemove_clicked( self, widget ):
        selection = self.repoMirrorsView.view.get_selection()
        urldata = selection.get_selected()
        if urldata[1] != None:
            self.repoMirrorsView.remove(urldata)

    def on_repoMirrorEdit_clicked( self, widget ):
        selection = self.repoMirrorsView.view.get_selection()
        urldata = selection.get_selected()
        # get text
        if urldata[1] != None:
            text = self.repoMirrorsView.get_text(urldata)
            self.repoMirrorsView.remove(urldata)
            text = inputBox(self.addrepo_ui.addRepoWin, _("Insert URL"), _("Enter a download mirror, HTTP or FTP")+"   ", input_text = text)
            # call liststore and tell to add
            self.repoMirrorsView.add(text)

    def on_addRepo_clicked( self, widget ):
        self.addrepo_ui.repoSubmit.show()
        self.addrepo_ui.repoSubmitEdit.hide()
        self.addrepo_ui.repoInsert.show()
        self.addrepo_ui.repoidEntry.set_editable(True)
        self.addrepo_ui.repodbcformatEntry.set_active(0)
        self.addrepo_ui.repoidEntry.set_text("")
        self.addrepo_ui.repoDescEntry.set_text("")
        self.addrepo_ui.repodbEntry.set_text("")
        self.addrepo_ui.addRepoWin.show()
        self.repoMirrorsView.populate()

    def __loadRepodata(self, repodata):
        self.addrepo_ui.repoidEntry.set_text(repodata['repoid'])
        self.addrepo_ui.repoDescEntry.set_text(repodata['description'])
        self.repoMirrorsView.store.clear()
        for x in repodata['plain_packages']:
            self.repoMirrorsView.add(x)
        idx = 0
        # XXX hackish way fix it
        while idx < 100:
            self.addrepo_ui.repodbcformatEntry.set_active(idx)
            if repodata['dbcformat'] == self.addrepo_ui.repodbcformatEntry.get_active_text():
                break
            idx += 1
        self.addrepo_ui.repodbEntry.set_text(repodata['plain_database'])

    def on_repoSubmitEdit_clicked( self, widget ):
        repodata = self.__getRepodata()
        errors = self.__validateRepoSubmit(repodata, edit = True)
        if errors:
            msg = "%s: %s" % (_("Wrong entries, errors"),', '.join(errors),)
            okDialog( self.addrepo_ui.addRepoWin, msg )
            return True
        else:
            disable = False
            if etpRepositoriesExcluded.has_key(repodata['repoid']):
                disable = True
            self.Equo.removeRepository(repodata['repoid'], disable = disable)
            if not disable:
                self.Equo.addRepository(repodata)
            self.setupRepoView()
            self.addrepo_ui.addRepoWin.hide()
            msg = "%s '%s' %s" % (_("You should press the button"),_("Regenerate Cache"),_("now"))
            okDialog( self.ui.main, msg )

    def __validateRepoSubmit(self, repodata, edit = False):
        errors = []
        if not repodata['repoid']:
            errors.append(_('No Repository Identifier'))
        if repodata['repoid'] and etpRepositories.has_key(repodata['repoid']):
            if not edit:
                errors.append(_('Duplicated Repository Identifier'))
        if not repodata['description']:
            repodata['description'] = "No description"
        if not repodata['plain_packages']:
            errors.append(_("No download mirrors"))
        if not repodata['plain_database'] or not (repodata['plain_database'].startswith("http://") or repodata['plain_database'].startswith("ftp://") or repodata['plain_database'].startswith("file://")):
            errors.append(_("Database URL must start either with http:// or ftp:// or file://"))
        return errors

    def __getRepodata(self):
        repodata = {}
        repodata['repoid'] = self.addrepo_ui.repoidEntry.get_text()
        repodata['description'] = self.addrepo_ui.repoDescEntry.get_text()
        repodata['plain_packages'] = self.repoMirrorsView.get_all()
        repodata['dbcformat'] = self.addrepo_ui.repodbcformatEntry.get_active_text()
        repodata['plain_database'] = self.addrepo_ui.repodbEntry.get_text()
        return repodata

    def on_repoSubmit_clicked( self, widget ):
        repodata = self.__getRepodata()
        # validate
        errors = self.__validateRepoSubmit(repodata)
        if not errors:
            self.Equo.addRepository(repodata)
            self.setupRepoView()
            self.addrepo_ui.addRepoWin.hide()
            msg = "%s '%s' %s" % (_("You should press the button"),_("Update Repositories"),_("now"))
            okDialog( self.ui.main, msg )
        else:
            msg = "%s: %s" % (_("Wrong entries, errors"),', '.join(errors),)
            okDialog( self.addrepo_ui.addRepoWin, msg )

    def on_addRepoWin_delete_event(self, widget, path):
        return True

    def on_repoCancel_clicked( self, widget ):
        self.addrepo_ui.addRepoWin.hide()

    def on_repoInsert_clicked( self, widget ):
        text = inputBox(self.addrepo_ui.addRepoWin, _("Insert Repository"), _("Insert Repository identification string")+"   ")
        if text:
            if (text.startswith("repository|")) and (len(text.split("|")) == 5):
                # filling dict
                textdata = text.split("|")
                repodata = {}
                repodata['repoid'] = textdata[1]
                repodata['description'] = textdata[2]
                repodata['packages'] = textdata[3].split()
                repodata['database'] = textdata[4].split("#")[0]
                dbcformat = textdata[4].split("#")[-1]
                if dbcformat in etpConst['etpdatabasesupportedcformats']:
                    repodata['dbcformat'] = dbcformat
                else:
                    repodata['dbcformat'] = etpConst['etpdatabasesupportedcformats'][0]
                # fill window
                self.__loadRepodata(repodata)
            else:
                okDialog( self.addrepo_ui.addRepoWin, _("This Repository identification string is malformed") )

    def on_removeRepo_clicked( self, widget ):
        # get selected repo
        selection = self.repoView.view.get_selection()
        repodata = selection.get_selected()
        # get text
        if repodata[1] != None:
            repoid = self.repoView.get_repoid(repodata)
            if repoid == etpConst['officialrepositoryid']:
                okDialog( self.ui.main, _("You! Why do you want to remove the main repository ?"))
                return True
            self.Equo.removeRepository(repoid)
            self.setupRepoView()
            msg = "%s '%s' %s '%s' %s" % (_("You must now either press the"),_("Update Repositories"),_("or the"),_("Regenerate Cache"),_("now"))
            okDialog( self.ui.main, msg )

    def on_repoEdit_clicked( self, widget ):
        self.addrepo_ui.repoSubmit.hide()
        self.addrepo_ui.repoSubmitEdit.show()
        self.addrepo_ui.repoInsert.hide()
        self.addrepo_ui.repoidEntry.set_editable(False)
        # get selection
        selection = self.repoView.view.get_selection()
        repostuff = selection.get_selected()
        if repostuff[1] != None:
            repoid = self.repoView.get_repoid(repostuff)
            repodata = self.Equo.entropyTools.getRepositorySettings(repoid)
            self.__loadRepodata(repodata)
            self.addrepo_ui.addRepoWin.show()

    def on_terminal_clear_activate(self, widget):
        self.output.text_written = []
        self.console.reset()

    def on_terminal_copy_activate(self, widget):
        self.clipboard.clear()
        self.clipboard.set_text(''.join(self.output.text_written))

    def on_PageButton_pressed( self, widget, page ):
        pass

    def on_PageButton_changed( self, widget, page ):
        ''' Left Side Toolbar Handler'''
        # do not put here actions for 'packages' and 'output' but use on_PageButton_pressed
        if page == "filesconf":
            self.populateFilesUpdate()
        elif page == "glsa":
            self.populateAdvisories(None,'affected')
        self.setNotebookPage(const.PAGES[page])

    def on_pkgFilter_toggled(self,rb,action):
        ''' Package Type Selection Handler'''
        if rb.get_active(): # Only act on select, not deselect.
            rb.grab_add()
            self.lastPkgPB = action
            # Only show add/remove all when showing updates
            if action == 'updates':
                self.ui.pkgSelect.show()
                self.ui.pkgDeSelect.show()
            else:
                self.ui.pkgSelect.hide()
                self.ui.pkgDeSelect.hide()

            self.addPackages()
            rb.grab_remove()

    def on_repoRefresh_clicked(self,widget):
        repos = self.repoView.get_selected()
        if not repos:
            okDialog( self.ui.main, _("Please select at least one repository") )
            return
        self.setPage('output')
        self.logger.info( "Enabled repositories : %s" % ",".join(repos))
        self.startWorking()
        self.updateRepositories(repos)
        # clear cache here too
        self.endWorking()
        self.etpbase.clearCache()
        self.setupRepoView()
        self.setupSpritz()
        self.setupAdvisories()
        self.setPage('repos')

    def on_cacheButton_clicked(self,widget):
        self.repoView.get_selected()
        self.setPage('output')
        self.logger.info( "Cleaning cache")
        self.cleanEntropyCaches(alone = True)

    def on_repoDeSelect_clicked(self,widget):
        self.repoView.deselect_all()


    def on_queueDel_clicked( self, widget ):
        """ Delete from Queue Button Handler """
        self.queueView.deleteSelected()

    def on_queueQuickAdd_activate(self,widget):
        txt = widget.get_text()
        arglist = txt.split(' ')
        self.doQuickAdd(arglist[0],arglist[1:])

    def on_queueProcess_clicked( self, widget ):
        """ Process Queue Button Handler """
        if self.queue.total() == 0: # Check there are any packages in the queue
            self.setStatus(_('No packages in queue'))
            return

        rc = self.processPackageQueue(self.queue.packages)
        if rc:
            self.queue.clear()       # Clear package queue
            self.queueView.refresh() # Refresh Package Queue

    def on_queueSave_clicked( self, widget ):
        dialog = gtk.FileChooserDialog(title=None,action=gtk.FILE_CHOOSER_ACTION_SAVE,
                                  buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,gtk.STOCK_SAVE,gtk.RESPONSE_OK))
        homedir = os.getenv('HOME')
        if not homedir:
            homedir = "/tmp"
        dialog.set_current_folder(homedir)
        dialog.set_current_name('queue.spritz')
        response = dialog.run()
        if response == gtk.RESPONSE_OK:
            fn = dialog.get_filename()
        elif response == gtk.RESPONSE_CANCEL:
            fn = None
        dialog.destroy()
        if fn:
            self.logger.info("Saving queue to %s" % fn)
            pkgdata = self.queue.get()
            keys = pkgdata.keys()
            for key in keys:
                if pkgdata[key]:
                    pkgdata[key] = [str(x) for x in pkgdata[key]]
            self.Equo.dumpTools.dumpobj(fn,pkgdata,True)

    def on_queueOpen_clicked( self, widget ):
        dialog = gtk.FileChooserDialog(title=None,action=gtk.FILE_CHOOSER_ACTION_OPEN,
                                  buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,gtk.STOCK_OPEN,gtk.RESPONSE_OK))
        homedir = os.getenv('HOME')
        if not homedir:
            homedir = "/tmp"
        dialog.set_current_folder(homedir)
        response = dialog.run()
        if response == gtk.RESPONSE_OK:
            fn = dialog.get_filename()
        elif response == gtk.RESPONSE_CANCEL:
            fn = None
        dialog.destroy()
        if fn:
            self.logger.info("Loading queue from %s" % fn)
            try:
                pkgdata = self.Equo.dumpTools.loadobj(fn,True)
            except:
                return

            try:
                pkgdata_keys = pkgdata.keys()
            except:
                return
            packages = self.etpbase.getAllPackages()
            collected_items = []
            for key in pkgdata_keys:
                for pkg in pkgdata[key]:
                    found = False
                    for x in packages:
                        if (pkg == str(x)) and (x.action == key):
                            found = True
                            collected_items.append([key,x])
                            break
                    if not found:
                        okDialog( self.ui.main, _("Queue is too old. Cannot load.") )
                        return

            for pkgtuple in collected_items:
                key = pkgtuple[0]
                pkg = pkgtuple[1]
                pkg.queued = key
                self.queue.packages[key].append(pkg)

            self.queueView.refresh()

    def loadAdvInfoMenu(self, item):

        key, affected, data = item

        adv_pixmap = const.PIXMAPS_PATH+'/button-glsa.png'
        self.advinfo_ui.advImage.set_from_file(adv_pixmap)

        glsa_idtext = "<b>GLSA</b>#<span foreground='#FF0000' weight='bold'>%s</span>" % (key,)
        self.advinfo_ui.labelIdentifier.set_markup(glsa_idtext)

        bold_items = [
                        self.advinfo_ui.descriptionLabel,
                        self.advinfo_ui.backgroundLabel,
                        self.advinfo_ui.impactLabel,
                        self.advinfo_ui.affectedLabel,
                        self.advinfo_ui.bugsLabel,
                        self.advinfo_ui.referencesLabel,
                        self.advinfo_ui.revisedLabel,
                        self.advinfo_ui.announcedLabel,
                        self.advinfo_ui.synopsisLabel,
                        self.advinfo_ui.workaroundLabel,
                        self.advinfo_ui.resolutionLabel
                     ]
        for item in bold_items:
            t = item.get_text()
            item.set_markup("<b>%s</b>" % (t,))

        # packages
        packages_data = data['affected']
        glsa_packages = '\n'.join([x for x in packages_data])
        glsa_packages = "<span weight='bold' size='large'>%s</span>" % (glsa_packages,)
        self.advinfo_ui.labelPackages.set_markup(glsa_packages)

        # title
        self.advinfo_ui.labelTitle.set_markup( "<small>%s\n<span foreground='#0000FF'>%s</span></small>" % (data['title'],data['url'],))

        # description
        desc_text = ' '.join([x.strip() for x in data['description'].split("\n")]).strip()
        if data['description_items']:
            for item in data['description_items']:
                desc_text += '\n\t%s %s' % ("<span foreground='#FF0000'>(*)</span>",item,)
        desc_text = "<small>%s</small>" % (desc_text,)
        self.advinfo_ui.descriptionTextLabel.set_markup(desc_text)

        # background
        back_text = ' '.join([x.strip() for x in data['background'].split("\n")]).strip()
        back_text = "<small>%s</small>" % (back_text,)
        self.advinfo_ui.backgroundTextLabel.set_markup(back_text)

        # impact
        impact_text = ' '.join([x.strip() for x in data['impact'].split("\n")]).strip()
        impact_text = "<small>%s</small>" % (impact_text,)
        self.advinfo_ui.impactTextLabel.set_markup(impact_text)
        t = self.advinfo_ui.impactLabel.get_text()
        t = "<b>%s</b>" % (t,)
        t += " [<span foreground='darkgreen'>%s</span>:<span foreground='#0000FF'>%s</span>|<span foreground='darkgreen'>%s</span>:<span foreground='#FF0000'>%s</span>]" % (
                    _("impact"),
                    data['impacttype'],
                    _("access"),
                    data['access'],
        )
        self.advinfo_ui.impactLabel.set_markup(t)

        # affected packages
        self.affectedModel.clear()
        self.affectedView.set_model( self.affectedModel )
        for key in data['affected']:
            affected_data = data['affected'][key][0]
            vul_atoms = affected_data['vul_atoms']
            unaff_atoms = affected_data['unaff_atoms']
            parent = self.affectedModel.append(None,[key])
            if vul_atoms:
                myparent = self.affectedModel.append(parent,[_('Vulnerables')])
                for atom in vul_atoms:
                    self.affectedModel.append(myparent,[cleanMarkupString(atom)])
            if unaff_atoms:
                myparent = self.affectedModel.append(parent,[_('Unaffected')])
                for atom in unaff_atoms:
                    self.affectedModel.append(myparent,[cleanMarkupString(atom)])

        # bugs
        self.bugsModel.clear()
        self.bugsView.set_model( self.bugsModel )
        for bug in data['bugs']:
            self.bugsModel.append([cleanMarkupString(bug)])

        self.referencesModel.clear()
        self.referencesView.set_model( self.referencesModel )
        for reference in data['references']:
            self.referencesModel.append([cleanMarkupString(reference)])

        # announcedTextLabel
        self.advinfo_ui.announcedTextLabel.set_markup(data['announced'])
        # revisedTextLabel
        self.advinfo_ui.revisedTextLabel.set_markup(data['revised'])

        # synopsis
        synopsis_text = ' '.join([x.strip() for x in data['synopsis'].split("\n")]).strip()
        synopsis_text = "<small>%s</small>" % (synopsis_text,)
        self.advinfo_ui.synopsisTextLabel.set_markup(synopsis_text)

        # workaround
        workaround_text = ' '.join([x.strip() for x in data['workaround'].split("\n")]).strip()
        workaround_text = "<small>%s</small>" % (workaround_text,)
        self.advinfo_ui.workaroundTextLabel.set_markup(workaround_text)

        # resolution
        resolution_text = []
        for resolution in data['resolution']:
            resolution_text.append(' '.join([x.strip() for x in resolution.split("\n")]).strip())
        resolution_text = '\n'.join(resolution_text)
        resolution_text = "<small>%s</small>" % (resolution_text,)
        self.advinfo_ui.resolutionTextLabel.set_markup(resolution_text)

        self.advinfo_ui.advInfo.show()


    def on_adv_doubleclick( self, widget, iterator, path ):
        """ Handle selection of row in package view (Show Descriptions) """
        ( model, iterator ) = widget.get_selection().get_selected()
        if model != None and iterator != None:
            data = model.get_value( iterator, 0 )
            if data:
                self.loadAdvInfoMenu(data)

    def loadPkgInfoMenu(self, pkg):
        mymenu = PkgInfoMenu(self.Equo, pkg, self.ui.main)
        mymenu.load()

    def on_pkg_doubleclick( self, widget, iterator, path ):
        """ Handle selection of row in package view (Show Descriptions) """
        ( model, iterator ) = widget.get_selection().get_selected()
        if model != None and iterator != None:
            pkg = model.get_value( iterator, 0 )
            if pkg:
                self.loadPkgInfoMenu(pkg)

    def on_license_double_clicked( self, widget, iterator, path ):
        """ Handle selection of row in package view (Show Descriptions) """
        ( model, iterator ) = widget.get_selection().get_selected()
        if model != None and iterator != None:
            license_identifier = model.get_value( iterator, 0 )
            found = False
            license_text = ''
            if license_identifier:
                repoid = self.pkgProperties_selected.matched_atom[1]
                if type(repoid) is int:
                    dbconn = self.Equo.clientDbconn
                else:
                    dbconn = self.Equo.openRepositoryDatabase(repoid)
                if dbconn.isLicensedataKeyAvailable(license_identifier):
                    license_text = dbconn.retrieveLicenseText(license_identifier)
                    found = True
            if found:
                # prepare textview
                mybuffer = gtk.TextBuffer()
                mybuffer.set_text(license_text)
                xml_licread = gtk.glade.XML( const.GLADE_FILE, 'licenseReadWindow',domain="entropy" )
                read_dialog = xml_licread.get_widget( "licenseReadWindow" )
                okReadButton = xml_licread.get_widget( "okReadButton" )
                okReadButton.connect( 'clicked', self.destroy_read_license_dialog )
                licenseView = xml_licread.get_widget( "licenseTextView" )
                licenseView.set_buffer(mybuffer)
                read_dialog.set_title(license_identifier+" license text")
                read_dialog.show_all()
                self.read_license_dialog = read_dialog

    def destroy_read_license_dialog( self, widget ):
        self.read_license_dialog.destroy()

    def on_advInfo_delete_event(self, widget, path):
        self.advinfo_ui.advInfo.hide()
        return True

    def on_select_clicked(self,widget):
        ''' Package Add All button handler '''
        self.setBusy()
        self.startWorking()
        self.wait_ui.waitWindow.show_all()
        busyCursor(self.wait_ui.waitWindow)
        self.pkgView.selectAll()
        self.endWorking()
        self.unsetBusy()
        normalCursor(self.wait_ui.waitWindow)
        self.wait_ui.waitWindow.hide()

    def on_deselect_clicked(self,widget):
        ''' Package Remove All button handler '''
        self.on_clear_clicked(widget)
        self.setBusy()
        self.pkgView.deselectAll()
        self.unsetBusy()

    def on_skipMirror_clicked(self,widget):
        if self.skipMirror:
            self.logger.info('skipmirror')
            self.skipMirrorNow = True

    def on_search_clicked(self,widget):
        ''' Search entry+button handler'''
        txt = self.ui.pkgFilter.get_text()
        flt = filters.spritzFilter.get('KeywordFilter')
        if txt != '':
            flt.activate()
            lst = txt.split(' ')
            self.logger.debug('Search Keyword : %s' % ','.join(lst))
            flt.setKeys(lst)
        else:
            flt.activate(False)
        action = self.lastPkgPB
        rb = self.packageRB[action]
        self.on_pkgFilter_toggled(rb,action)

    def on_clear_clicked(self,widget):
        ''' Search Clear button handler'''
        self.ui.pkgFilter.set_text("")
        self.on_search_clicked(None)

    def on_comps_cursor_changed(self, widget):
        self.setBusy()
        """ Handle selection of row in Comps Category  view  """
        ( model, iterator ) = widget.get_selection().get_selected()
        if model != None and iterator != None:
            myid = model.get_value( iterator, 0 )
            self.populateCategoryPackages(myid)
        self.unsetBusy()

    def on_FileQuit( self, widget ):
        self.quit()

    def on_HelpAbout( self, widget = None ):
        about = AboutDialog(const.PIXMAPS_PATH+'/spritz-about.png',const.CREDITS,self.settings.branding_title)
        about.show()

class SpritzApplication(SpritzController,SpritzGUI):

    def __init__(self):

        self.Equo = EquoConnection
        locked = self.Equo._resources_run_check_lock()
        if locked:
            okDialog( None, _("Entropy resources are locked and not accessible. " + \
                "Another Entropy application is running. Sorry, can't load Spritz.") )
            sys.exit(1)

        SpritzController.__init__( self )
        SpritzGUI.__init__(self, self.Equo, self.etpbase)
        self.logger = logging.getLogger("yumex.main")

        # init flags
        self.skipMirror = False
        self.skipMirrorNow = False
        self.doProgress = False
        self.categoryOn = False
        self.quitNow = False
        self.isWorking = False
        self.logger.info(_('Entropy Config Setup'))
        self.catsView.etpbase = self.etpbase
        self.lastPkgPB = "updates"
        self.etpbase.setFilter(filters.spritzFilter.processFilters)

        self.Equo.connect_to_gui(self.progress, self.progressLogWrite, self.output)
        self.setupEditor()
        # Setup GUI
        self.setupGUI()
        self.setPage("packages")

        self.setupAdvisories()
        self.logger.info(_('GUI Setup Completed'))
        # setup Repositories
        self.setupRepoView()
        self.firstTime = True
        # calculate updates
        self.setupSpritz()

        self.console.set_pty(self.pty[0])
        self.resetProgressText()
        self.pkgProperties_selected = None
        self.setupAdvPropertiesView()

    def setupAdvisories(self):
        self.Advisories = self.Equo.Security()

    def setupAdvPropertiesView(self):

        # affected view
        self.affectedView = self.advinfo_ui.affectedView
        self.affectedModel = gtk.TreeStore( gobject.TYPE_STRING )
        cell = gtk.CellRendererText()
        column = gtk.TreeViewColumn( _( "Package" ), cell, markup = 0 )
        self.affectedView.append_column( column )
        self.affectedView.set_model( self.affectedModel )

        # bugs view
        self.bugsView = self.advinfo_ui.bugsView
        self.bugsModel = gtk.ListStore( gobject.TYPE_STRING )
        cell = gtk.CellRendererText()
        column = gtk.TreeViewColumn( _( "Bug" ), cell, markup = 0 )
        self.bugsView.append_column( column )
        self.bugsView.set_model( self.bugsModel )

        # references view
        self.referencesView = self.advinfo_ui.referencesView
        self.referencesModel = gtk.ListStore( gobject.TYPE_STRING )
        cell = gtk.CellRendererText()
        column = gtk.TreeViewColumn( _( "Reference" ), cell, markup = 0 )
        self.referencesView.append_column( column )
        self.referencesView.set_model( self.referencesModel )

    def setupEditor(self):

        pathenv = os.getenv("PATH")
        if os.path.isfile("/etc/profile.env"):
            f = open("/etc/profile.env")
            env_file = f.readlines()
            for line in env_file:
                line = line.strip()
                if line.startswith("export PATH='"):
                    line = line[len("export PATH='"):]
                    line = line.rstrip("'")
                    for path in line.split(":"):
                        pathenv += ":"+path
                    break
        os.environ['PATH'] = pathenv

        self.fileEditor = '/usr/bin/xterm -e $EDITOR'
        de_session = os.getenv('DESKTOP_SESSION')
        if de_session == None: de_session = ''
        path = os.getenv('PATH').split(":")
        if os.access("/usr/bin/xdg-open",os.X_OK):
            self.fileEditor = "/usr/bin/xdg-open"
        if de_session.find("kde") != -1:
            for item in path:
                itempath = os.path.join(item,'kwrite')
                itempath2 = os.path.join(item,'kedit')
                itempath3 = os.path.join(item,'kate')
                if os.access(itempath,os.X_OK):
                    self.fileEditor = itempath
                    break
                elif os.access(itempath2,os.X_OK):
                    self.fileEditor = itempath2
                    break
                elif os.access(itempath3,os.X_OK):
                    self.fileEditor = itempath3
                    break
        else:
            if os.access('/usr/bin/gedit',os.X_OK):
                self.fileEditor = '/usr/bin/gedit'

    def startWorking(self):
        self.isWorking = True
        busyCursor(self.ui.main)
        self.ui.progressVBox.grab_add()
        gtkEventThread.startProcessing()

    def endWorking(self):
        self.isWorking = False
        self.ui.progressVBox.grab_remove()
        normalCursor(self.ui.main)
        gtkEventThread.endProcessing()

    def setupSpritz(self):
        msg = _('Generating metadata. Please wait.')
        self.setStatus(msg)
        self.addPackages()
        self.populateCategories()
        msg = _('Ready')
        self.setStatus(msg)

    def cleanEntropyCaches(self, alone = False):
        if alone:
            self.progress.total.hide()
        self.Equo.generate_cache(depcache = True, configcache = False)
        # clear views
        self.etpbase.clearPackages()
        self.etpbase.clearCache()
        self.setupSpritz()
        if alone:
            self.progress.total.show()

    def populateAdvisories(self, widget, show):
        self.setBusy()
        cached = None
        try:
            cached = self.Advisories.get_advisories_cache()
        except (IOError, EOFError):
            pass
        except AttributeError:
            time.sleep(5)
            cached = self.Advisories.get_advisories_cache()
        if cached == None:
            try:
                cached = self.Advisories.get_advisories_metadata()
            except Exception, e:
                okDialog( self.ui.main, "%s: %s" % (_("Error loading advisories"),e) )
                cached = {}
        if cached:
            self.advisoriesView.populate(self.Advisories, cached, show)
        self.unsetBusy()

    def populateFilesUpdate(self):
        # load filesUpdate interface and fill self.filesView
        cached = None
        try:
            cached = self.Equo.FileUpdates.load_cache()
        except exceptionTools.CacheCorruptionError:
            pass
        if cached == None:
            self.setBusy()
            cached = self.Equo.FileUpdates.scanfs()
            self.unsetBusy()
        if cached:
            self.filesView.populate(cached)

    def updateRepositories(self, repos):

        # set steps
        progress_step = float(1)/(len(repos))
        step = progress_step
        myrange = []
        while progress_step < 1.0:
            myrange.append(step)
            progress_step += step
        myrange.append(step)

        self.progress.total.setup( myrange )
        self.progress.set_mainLabel(_('Initializing Repository module...'))
        forceUpdate = self.ui.forceRepoUpdate.get_active()

        try:
            repoConn = self.Equo.Repositories(repos, forceUpdate = forceUpdate)
        except exceptionTools.PermissionDenied:
            self.progressLog(_('You must run this application as root'), extra = "repositories")
            return 1
        except exceptionTools.MissingParameter:
            msg = "%s: %s" % (_('No repositories specified in'),etpConst['repositoriesconf'],)
            self.progressLog( msg, extra = "repositories")
            return 127
        except exceptionTools.OnlineMirrorError:
            self.progressLog(_('You are not connected to the Internet. You should.'), extra = "repositories")
            return 126
        except Exception, e:
            msg = "%s: %s" % (_('Unhandled exception'),e,)
            self.progressLog(msg, extra = "repositories")
            return 2
        rc = repoConn.sync()
        if repoConn.syncErrors or (rc != 0):
            self.progress.set_mainLabel(_('Errors updating repositories.'))
            self.progress.set_subLabel(_('Please check logs below for more info'))
        else:
            if repoConn.alreadyUpdated == 0:
                self.progress.set_mainLabel(_('Repositories updated successfully'))
            else:
                if len(repos) == repoConn.alreadyUpdated:
                    self.progress.set_mainLabel(_('All the repositories were already up to date.'))
                else:
                    msg = "%s %s" % (repoConn.alreadyUpdated,_("repositories were already up to date. Others have been updated."),)
                    self.progress.set_mainLabel(msg)
            if repoConn.newEquo:
                self.progress.set_extraLabel(_('sys-apps/entropy needs to be updated as soon as possible.'))

        initConfig_entropyConstants(etpSys['rootdir'])

    def resetProgressText(self):
        self.progress.set_mainLabel(_('Nothing to do. I am idle.'))
        self.progress.set_subLabel(_('Really, don\'t waste your time here. This is just a placeholder'))
        self.progress.set_extraLabel(_('I am still alive and kickin\''))

    def setupRepoView(self):
        self.repoView.populate()

    def setBusy(self):
        busyCursor(self.ui.main)

    def unsetBusy(self):
        normalCursor(self.ui.main)

    def addPackages(self):

        action = self.lastPkgPB
        if action == 'all':
            masks = ['installed','available']
        else:
            masks = [action]


        self.setBusy()
        bootstrap = False
        if (self.Equo.get_world_update_cache(empty_deps = False) == None):
            bootstrap = True
            self.setPage('output')
        self.progress.total.hide()

        self.etpbase.clearPackages()
        if bootstrap:
            self.etpbase.clearCache()
            self.startWorking()

        allpkgs = []
        if self.doProgress: self.progress.total.next() # -> Get lists
        self.progress.set_mainLabel(_('Generating Metadata, please wait.'))
        self.progress.set_subLabel(_('Entropy is indexing the repositories. It will take a few seconds'))
        self.progress.set_extraLabel(_('While you are waiting, take a break and look outside. Is it rainy?'))
        for flt in masks:
            msg = "%s: %s" % (_('Calculating'),flt,)
            self.setStatus(msg)
            allpkgs += self.etpbase.getPackages(flt)
            self.setStatus(_("Ready"))
        if self.doProgress: self.progress.total.next() # -> Sort Lists

        if action == "updates":
            msg = "%s: available" % (_('Calculating'),)
            self.setStatus(msg)
            self.etpbase.getPackages("available")
            self.setStatus(_("Ready"))

        if bootstrap:
            self.endWorking()

        empty = False
        if not allpkgs and action == "updates":
            allpkgs = self.etpbase.getPackages('fake_updates')
            empty = True

        self.pkgView.populate(allpkgs, empty = empty)
        self.progress.total.show()

        if self.doProgress: self.progress.hide() #Hide Progress
        if bootstrap:
            self.setPage('packages')

        self.unsetBusy()
        # reset labels
        self.resetProgressText()

    def processPackageQueue(self, pkgs):

        # preventive check against other instances
        locked = self.Equo.application_lock_check()
        if locked:
            okDialog(self.ui.main, _("Another Entropy instance is running. Cannot process queue."))
            self.progress.reset_progress()
            self.setPage('packages')
            return False

        self.setStatus( _( "Running tasks" ) )
        total = len( pkgs['i'] )+len( pkgs['u'] )+len( pkgs['r'] ) +len( pkgs['rr'] )
        state = True
        if total > 0:
            self.startWorking()
            self.progress.show()
            self.progress.set_mainLabel( _( "Processing Packages in queue" ) )

            queue = pkgs['i']+pkgs['u']+pkgs['rr']
            install_queue = [x.matched_atom for x in queue]
            removal_queue = [x.matched_atom[0] for x in pkgs['r']]
            do_purge_cache = set([x.matched_atom[0] for x in pkgs['r'] if x.do_purge])
            if install_queue or removal_queue:
                controller = QueueExecutor(self)
                e,i = controller.run(install_queue[:], removal_queue[:], do_purge_cache)
                if e != 0:
                    okDialog(   self.ui.main,
                                _("Attention. An error occured when processing the queue."
                                    "\nPlease have a look in the processing terminal.")
                    )
                self.endWorking()
                while gtk.events_pending():
                    time.sleep(0.1)
                self.etpbase.clearPackages()
                time.sleep(5)
            self.endWorking()
            self.progress.reset_progress()
            self.etpbase.clearPackages()
            self.etpbase.clearCache()
            self.Equo.closeAllRepositoryDatabases()
            self.Equo.reopenClientDbconn()
            # regenerate packages information

            self.setupSpritz()
            self.Equo.FileUpdates.scanfs(dcache = False)
            if self.Equo.FileUpdates.scandata:
                if len(self.Equo.FileUpdates.scandata) > 0:
                    self.setPage('filesconf')
            #self.progress.hide()
            return state
        else:
            self.setStatus( _( "No packages selected" ) )
            return state

    def populateCategories(self):
        self.setBusy()
        self.etpbase.populateCategories()
        self.catsView.populate(self.etpbase.getCategories())
        self.unsetBusy()

    def populateCategoryPackages(self, cat):
        pkgs = self.etpbase.getPackagesByCategory(cat)
        self.catPackages.populate(pkgs,self.ui.tvCatPackages)


if __name__ == "__main__":
    gtkEventThread = ProcessGtkEventsThread()
    try:
        gtkEventThread.start()
        try:
            gtk.window_set_default_icon_from_file(const.PIXMAPS_PATH+"/spritz-icon.png")
        except gobject.GError:
            pass
        mainApp = SpritzApplication()
        gtk.main()
    except SystemExit, e:
        print "Quit by User"
        gtkEventThread.doQuit()
        sys.exit(1)
    except KeyboardInterrupt:
        print "Quit by User"
        gtkEventThread.doQuit()
        sys.exit(1)
    except: # catch other exception and write it to the logger.
        logger = logging.getLogger('yumex.main')
        etype = sys.exc_info()[0]
        evalue = sys.exc_info()[1]
        etb = traceback.extract_tb(sys.exc_info()[2])
        logger.error('Error Type: %s' % str(etype) )
        errmsg = 'Error Type: %s \n' % str(etype)
        logger.error('Error Value: ' + str(evalue))
        errmsg += 'Error Value: %s \n' % str(evalue)
        logger.error('Traceback: \n')
        for tub in etb:
            f,l,m,c = tub # file,lineno, function, codeline
            logger.error('  File : %s , line %s, in %s' % (f,str(l),m))
            errmsg += '  File : %s , line %s, in %s\n' % (f,str(l),m)
            logger.error('    %s ' % c)
            errmsg += '    %s \n' % c
        import entropyTools
        conntest = entropyTools.get_remote_data(etpConst['conntestlink'])
        rc, (name,mail,description) = errorMessage( None,
                                                    _( "Exception caught" ),
                                                    _( "Spritz crashed! An unexpected error occured." ),
                                                    errmsg,
                                                    showreport = conntest
        )
        if rc == -1:
            error = ErrorReportInterface()
            error.prepare(errmsg, name, mail, description = description)
            result = error.submit()
            if result:
                okDialog(None,_("Your report has been submitted successfully! Thanks a lot."))
            else:
                okDialog(None,_("Cannot submit your report. Not connected to Internet?"))
        gtkEventThread.doQuit()
        sys.exit(1)

    gtkEventThread.doQuit()
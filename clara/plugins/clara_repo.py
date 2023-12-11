#!/usr/bin/env python
# -*- coding: utf-8 -*-
##############################################################################
#  Copyright (C) 2014-2017 EDF SA                                            #
#                                                                            #
#  This file is part of Clara                                                #
#                                                                            #
#  This software is governed by the CeCILL-C license under French law and    #
#  abiding by the rules of distribution of free software. You can use,       #
#  modify and/ or redistribute the software under the terms of the CeCILL-C  #
#  license as circulated by CEA, CNRS and INRIA at the following URL         #
#  "http://www.cecill.info".                                                 #
#                                                                            #
#  As a counterpart to the access to the source code and rights to copy,     #
#  modify and redistribute granted by the license, users are provided only   #
#  with a limited warranty and the software's author, the holder of the      #
#  economic rights, and the successive licensors have only limited           #
#  liability.                                                                #
#                                                                            #
#  In this respect, the user's attention is drawn to the risks associated    #
#  with loading, using, modifying and/or developing or reproducing the       #
#  software by the user in light of its specific status of free software,    #
#  that may mean that it is complicated to manipulate, and that also         #
#  therefore means that it is reserved for developers and experienced        #
#  professionals having in-depth computer knowledge. Users are therefore     #
#  encouraged to load and test the software's suitability as regards their   #
#  requirements in conditions enabling the security of their systems and/or  #
#  data to be ensured and, more generally, to use and operate it in the      #
#  same conditions as regards security.                                      #
#                                                                            #
#  The fact that you are presently reading this means that you have had      #
#  knowledge of the CeCILL-C license and that you accept its terms.          #
#                                                                            #
##############################################################################
"""
Creates, updates and synchronizes local Debian repositories.

Usage:
    clara repo key
    clara repo init <dist>
    clara repo sync (all|<dist> [<suites>...])
    clara repo push [<dist>]
    clara repo add <dist> <file>... [--reprepro-flags="list of flags"...] [--no-push]
    clara repo del <dist> <name>... [--no-push]
    clara repo list (all|<dist>)
    clara repo search <keyword>
    clara repo copy <dist> <package> <from-dist> [--no-push]
    clara repo move <dist> <package> <from-dist> [--no-push]
    clara repo jenkins <dist> <job> [--source=<arch>] [--reprepro-flags="list of flags"...] [--build=<build>]
    clara repo -h | --help | help

Options:
    <dist> is the target distribution
    <file> can be one or more *.deb binaries, *.changes files or *.dsc files.
    <name> is the package to remove, if the package is a source name, it'll
    remove all the associated binaries
"""

import subprocess
import logging
import os
import sys
import tempfile
import configparser
import re
import glob
import shutil

import docopt
from clara.utils import clara_exit, run, get_from_config, get_from_config_or, value_from_file, conf, os_distribution, os_major_version

_opt = {'dist': None}

def do_update(path_repo):
    fnull = open(os.devnull, 'w')
    cmd = ["/usr/bin/createrepo", "--update", path_repo]
    run(cmd, stdout=fnull, stderr=fnull)
    cmd = ["/usr/bin/yum-config-manager", "--enable", _opt['dist']]
    run(cmd, stdout=fnull, stderr=fnull)
    fnull.close()

def do_create(dest_dir="Packages"):
    # default to "/srv/repos" distribution base repository
    repo_dir = get_from_config_or("repo", "repo_rpm", _opt['dist'], '/srv/repos')
    path_repo = os.path.join(repo_dir, _opt['dist'])

    if os.path.isdir(path_repo):
        clara_exit("The repository '{}' already exists!".format(path_repo))

    logging.info("Creating repository {} in directory {} ...".format(_opt['dist'], repo_dir))
    os.makedirs(os.path.join(path_repo, dest_dir))

    do_update(path_repo)

    createrepo_config = os.path.join("/etc/yum/repos.d/", _opt['dist'] + ".repo")
    fcreaterepo = open(createrepo_config, 'w')
    fcreaterepo.write("""[{0}]
name={0}
baseurl={1}
enabled=1
autorefresh=1
gpgcheck=1
""".format(_opt['dist'], "file://" + path_repo))

    fcreaterepo.close()

# Returns a boolean to tell if password derivation can be used with OpenSSL.
# It is disabled on Debian < 10 (eg. in stretch) because it is not supported by
# openssl provided in these old distributions.
#
# This code can be safely removed when Debian 9 stretch support is dropped.
def enable_password_derivation():

    return os_distribution() != 'debian' or os_major_version() > 9

def do_key():
    key = get_from_config("repo", "gpg_key")
    fnull = open(os.devnull, 'w')
    cmd = ['gpg', '--list-secret-keys', key]
    logging.debug("repo/do_key: {0}".format(" ".join(cmd)))
    retcode = subprocess.call(cmd, stdout=fnull, stderr=fnull)
    fnull.close()

    # We import the key if it hasn't been imported before
    if retcode != 0:
        file_stored_key = get_from_config("repo", "stored_enc_key")
        if os.path.isfile(file_stored_key):
            password = value_from_file(get_from_config("common", "master_passwd_file"), "ASUPASSWD")

            digest = get_from_config_or("common", "digest_type", default="sha256")
            if digest not in ['md2', 'md5', 'mdc2', 'rmd160', 'sha', 'sha1', 'sha224', 'sha256', 'sha384', 'sha512']:
                logging.warning("Invalid digest type : {0}, using default digest type: sha256".format(digest))
                digest = "sha256"

            if len(password) > 20:
                fdesc, temp_path = tempfile.mkstemp(prefix="tmpClara")
                cmd = ['openssl', 'enc', '-aes-256-cbc', '-md', digest, '-d', '-in', file_stored_key, '-out', temp_path, '-k', password]

                # Return the openssl command to proceed with operation op, with or without key
                # derivation.
                if enable_password_derivation():
                    # The number of iterations is hard-coded as it must be changed
                    # synchronously on both clara and puppet-hpc for seamless handling of
                    # encrypted files. It is set explicitely to avoid relying on openssl
                    # default value and being messed by sudden change of this default
                    # value.
                    cmd[3:3] = ['-iter', '+100000', '-pbkdf2' ]

                logging.debug("repo/do_key: {0}".format(" ".join(cmd)))
                retcode = subprocess.call(cmd)

                if retcode != 0:
                    os.close(fdesc)
                    os.remove(temp_path)
                    clara_exit('Command failed {0}'.format(" ".join(cmd)))
                else:
                    logging.info("Trying to import key {0}".format(key))
                    fnull = open(os.devnull, 'w')
                    cmd = ['gpg', '--allow-secret-key-import', '--import', temp_path]
                    logging.debug("repo/do_key: {0}".format(" ".join(cmd)))
                    retcode = subprocess.call(cmd)
                    fnull.close()
                    os.close(fdesc)
                    os.remove(temp_path)
                    if retcode != 0:
                        logging.info("\nThere was a problem with the import, make sure the key you imported "
                              "from {0} is the same you have in your configuration: {1}".format(file_stored_key, key))

            else:
                clara_exit("There was some problem while reading ASUPASSWD's value")
        else:
            clara_exit('Unable to read:  {0}'.format(file_stored_key))
    else:
        logging.info("GPG key was already imported.")


def do_init():
    repo_dir = get_from_config("repo", "repo_dir", _opt['dist'])
    reprepro_config = repo_dir + '/conf/distributions'
    mirror_local = get_from_config("repo", "mirror_local", _opt['dist'])
    if (mirror_local=="" or mirror_local==None):
        mirror_local = repo_dir +'/mirror'

    if os.path.isdir(repo_dir):
        clara_exit("The repository '{0}' already exists !".format(repo_dir))
    else :
        if not os.path.isfile(reprepro_config):

            if not os.path.isdir(repo_dir + '/conf'):
                os.makedirs(repo_dir + '/conf')

            freprepro = open(reprepro_config, 'w')
            freprepro.write("""Origin: {0}
Label: {1}
Suite: {1}
Codename: {1}
Version: {2}
Architectures: amd64 source
Components: main contrib non-free
UDebComponents: main
SignWith: {3}
Description: Depot Local {4}
DebIndices: Packages Release . .gz .bz2
DscIndices: Sources Release . .gz .bz2
""".format(get_from_config("common", "origin", _opt['dist']),
                    _opt['dist'],
                    get_from_config("repo", "version", _opt['dist']),
                    get_from_config("repo", "gpg_key", _opt['dist']),
                    get_from_config("repo", "clustername", _opt['dist'])))
            freprepro.close()

            os.chdir(repo_dir)

            list_flags = ['--ask-passphrase']
            if conf.ddebug:
                list_flags.append("-V")
            try:

                run(['reprepro'] + list_flags +
                    ['--basedir', repo_dir,
                    '--outdir', mirror_local,
                    'export', _opt['dist']])
            except:
                shutil.rmtree(repo_dir)
                clara_exit("The repository '{0}' has not been initialized properly, it will be deleted  !".format(repo_dir))


def get(config, section, value):

    # If the value is not in the override section, look in "repos" from the config.ini
    if config.has_option(section, value):
        return config.get(section, value).strip()
    else:
        try:
            return get_from_config("repo", value)
        except:
            clara_exit("Value '{0}' not found in section '{1}'".format(value, section))


def do_sync(selected_dist, input_suites=[]):

    suites = []
    # Sync everything
    if selected_dist == 'all':
        for d in get_from_config("common", "allowed_distributions").split(","):
            suites = suites + get_from_config("repo", "suites", d).split(",")
    else:
        # Only sync suites related to the selected distribution
        if len(input_suites) == 0:
            suites = get_from_config("repo", "suites", selected_dist).split(",")
        # User selected one or several suites, check that they are valid
        else:
            for s in input_suites:
                if s not in get_from_config("repo", "suites", selected_dist).split(","):
                    clara_exit("{0} is not a valid suite from distribution {1}.\n"
                        "Valid suites are: {2}".format(
                         s, selected_dist, get_from_config("repo", "suites", selected_dist)))
            suites = input_suites
    suites = [suite for suite in suites if suite]
    logging.debug("The suites to sync are: {0}.".format(" ".join(suites)))

    # Read /etc/clara/repos.ini
    if not os.path.isfile('/etc/clara/repos.ini'):
        clara_exit("File /etc/clara/repos.ini not found.")
    repos = configparser.ConfigParser()
    repos.read("/etc/clara/repos.ini")

    for s in suites:

        extra = []
        if conf.ddebug:  # if extra debug for 3rd party software
            extra = ['--debug']

        final_dir = get(repos, s, "mirror_root") + "/" + s
        run(['debmirror'] + extra + ["--diff=none",
            "--nosource", "--ignore-release-gpg", "--ignore-missing-release",
            "--method={0}".format(get(repos, s, "method")),
            "--arch={0}".format(get(repos, s, "archs")),
            "--host={0}".format(get(repos, s, "server")),
            "--root={0}".format(get(repos, s, "mirror_dir")),
            "--dist={0}".format(get(repos, s, "suite_name")),
            "--section={0}".format(get(repos, s, "sections")),
            final_dir])


def do_push(dist=''):
    push_cmd = get_from_config_or("repo", "push", dist, None)
    if push_cmd:
        push_hosts = get_from_config_or("repo", "hosts", dist, '').split(',')
        if push_hosts and push_hosts[0] is not '':
            for host in push_hosts:
                run(push_cmd.format(host).split(' '))
        else:
            run(push_cmd.split(' '))


def do_reprepro(action, package=None, flags=None, extra=None):
    repo_dir = get_from_config("repo", "repo_dir", _opt['dist'])
    reprepro_config = repo_dir + '/conf/distributions'
    mirror_local = get_from_config("repo", "mirror_local", _opt['dist'])
    if (mirror_local=="" or mirror_local==None):
        mirror_local = repo_dir +'/mirror'
    oldMask = os.umask(0o022)
    if not os.path.isfile(reprepro_config):
        clara_exit("There is not configuration for the local repository for {0}. Run first 'clara repo init <dist>'".format(_opt['dist']))

    list_flags = ['--silent', '--ask-passphrase']
    if conf.ddebug:
        list_flags = ['-V', '--ask-passphrase']

    if flags is not None:
        list_flags.append(flags)

    cmd = ['reprepro'] + list_flags + \
         ['--basedir', get_from_config("repo", "repo_dir", _opt['dist']),
         '--outdir', mirror_local,
         action]

    if extra is not None:
        for e in extra:
            cmd.append(e)
    else:
        if action in ['includedeb', 'include', 'includedsc', 'remove', 'removesrc', 'list']:
            cmd.append(_opt['dist'])

        if package is not None:
            cmd.append(package)
    run(cmd)
    os.umask(oldMask)


def copy_jenkins(job, arch, flags=None, build="lastSuccessfulBuild"):

    if re.search(r"bin-|-binaries", job):
        jobs = [job]
    else:
        jobs  = [job + "-binaries", "bin-" + job]

    jenkins_dir = get_from_config("repo", "jenkins_dir")
    isok = False

    for job in jobs:
        archive_path = "builds/%s/archive/" % build
        conf = "configurations/"
        axis_arch = conf + "axis-architecture/%s/" % arch
        paths = [ os.path.join(jenkins_dir, job, archive_path),
                  os.path.join(jenkins_dir, job, conf + archive_path),
                  os.path.join(jenkins_dir, job, axis_arch + archive_path)]

        for path in paths:

            if not os.path.isdir(path):
                continue

            message = "Found job named {} under path:\n{} ..!\n".format(job, path)
            logging.info(message)

            for changesfile in glob.glob(path + "/*_%s.changes" % arch):
                do_reprepro('include', package=changesfile, flags=flags)
                isok = True
                break

            # if any, continue breaking, are we are in nested break!
            if isok:
                break

        # if any, continue breaking, are we are in nested break!
        if isok:
            break

        message  = "No job named {} found! ".format(job)
        message += "Either is doesn't exist or needs to be built..!"
        logging.debug(message)



def main():
    logging.debug(sys.argv)
    dargs = docopt.docopt(__doc__)


    _opt['dist'] = get_from_config("common", "default_distribution")

    if dargs["<dist>"] is not None:
        _opt['dist'] = dargs["<dist>"]

    if _opt['dist'] not in get_from_config("common", "allowed_distributions"):
        clara_exit("{0} is not a known distribution".format(_opt['dist']))

    if re.match(r"scibian|calibre", _opt['dist']):
        distro = 'debian'
    elif "rpm-hpc" in _opt['dist']:
        distro = 'rhel'
    else:
        pattern = re.compile(r"(?P<distro>[a-z]+)(?P<version>\d+)")
        match = pattern.match(_opt['dist'])
        if match:
            distro = match.group('distro')
            if distro in ['rhel', 'centos', 'almalinux', 'rocky']:
                distro = 'rhel'
        else:
            # default to "rhel" distribution
            distro = get_from_config_or("repo", "distro", _opt['dist'], "rhel")
            if distro not in ['debian', 'rhel']:
                clara_exit("provided distribution %s not yet supported!" % distro)

    if dargs['key']:
        do_key()
    if dargs['init']:
        if distro == "debian":
            do_init()
        elif distro == "rhel":
            do_create()
    elif dargs['sync']:
        if dargs['all']:
            do_sync('all')
        else:
            do_sync(dargs['<dist>'], dargs['<suites>'])
    elif dargs['push']:
        get_from_config("repo", "push", _opt['dist'])
        if dargs['<dist>']:
            do_push(_opt['dist'])
        else:
            do_push()
    elif dargs['add']:
        for elem in dargs['<file>']:
            if elem.endswith(".deb"):
                do_reprepro('includedeb', elem, dargs['--reprepro-flags'])
            elif elem.endswith(".changes"):
                do_reprepro('include', elem, dargs['--reprepro-flags'])
            elif elem.endswith(".dsc"):
                do_reprepro('includedsc', elem, dargs['--reprepro-flags'])
            else:
                clara_exit("File is not a *.deb *.dsc or *.changes")
        if dargs['<file>'] and not dargs['--no-push']:
            do_push(_opt['dist'])
    elif dargs['del']:
        for elem in dargs['<name>']:
            do_reprepro('remove', elem)
            do_reprepro('removesrc', elem)
        if dargs['<name>'] and not dargs['--no-push']:
            do_push(_opt['dist'])
    elif dargs['list']:
        if dargs['all']:
            do_reprepro('dumpreferences')
        else:
            do_reprepro('list')
    elif dargs['search']:
        do_reprepro('ls', extra=[dargs['<keyword>']])
    elif dargs['copy']:
        if dargs['<from-dist>'] not in get_from_config("common", "allowed_distributions"):
            clara_exit("{0} is not a known distribution".format(dargs['<from-dist>']))
        do_reprepro('copy', extra=[_opt['dist'], dargs['<from-dist>'], dargs['<package>']])
        if not dargs['--no-push']:
            do_push(_opt['dist'])
    elif dargs['move']:
        if dargs['<from-dist>'] not in get_from_config("common", "allowed_distributions"):
            clara_exit("{0} is not a known distribution".format(dargs['<from-dist>']))
        do_reprepro('copy', extra=[_opt['dist'], dargs['<from-dist>'], dargs['<package>']])
        do_reprepro('remove', extra=[dargs['<from-dist>'], dargs['<package>']])
        do_reprepro('removesrc', extra=[dargs['<from-dist>'], dargs['<package>']])
        if not dargs['--no-push']:
            do_push(dargs['<from-dist>'])
            do_push(_opt['dist'])
    elif dargs['jenkins']:
        build = dargs['--build'] if dargs['--build'] else "lastSuccessfulBuild"
        arch = dargs['--source']
        if arch is None:
            arch = "amd64"
        copy_jenkins(dargs['<job>'], arch, flags=dargs['--reprepro-flags'], build=build)


if __name__ == '__main__':
    main()

import datetime
import fnmatch
import glob
import os
import re
import shlex
import shutil
import sys

from vee._vendor import pkg_resources

from vee import libs
from vee import log
from vee.builds import make_builder
from vee.cli import style
from vee.exceptions import AlreadyInstalled, AlreadyLinked
from vee.requirement import Requirement, requirement_parser
from vee.subproc import call
from vee.utils import cached_property, makedirs, linktree


class BasePackage(object):

    """Abstraction of a package manager.

    Packages are instances for each :class:`Requirement`, such that they are
    able to maintain state about that specific requirement.

    """

    type = 'base'


    def __init__(self, requirement=None, home=None):

        # Set from item access.
        if isinstance(requirement, dict):
            self.abstract_requirement = json.dumps(requirement, sort_keys=True)
            self.home = home
            for action in requirement_parser._actions:
                name = action.dest
                setattr(self, name, requirement.get(name, action.default))
        
        # Set from attr access.
        else:
            self.abstract_requirement = requirement and requirement.to_json()
            self.home = home or requirement.home
            for action in requirement_parser._actions:
                name = action.dest
                setattr(self, name, requirement and getattr(requirement, name))

        # A few need special handling
        self.environ = self.environ.copy() if self.environ else {}
        self.config = self.config[:] if self.config else []

        self._db_id = None
        self._db_link_id = None
        self.package_name = self.build_name = None

    def __repr__(self):
        return '<%s for %s>' % (
            self.__class__.__name__,
            self.abstract_requirement,
        )

    def freeze(self, environ=True):
        kwargs = {}
        for action in requirement_parser._actions:
            name = action.dest
            kwargs[name] = getattr(self, name)
        if environ:
            kwargs['environ'] = self.environ_diff
        return Requirement(home=self.home, **kwargs)

    def _resolve_environ(self, source=None):

        source = (source or os.environ).copy()
        source['VEE'] = self.home.root

        diff = {}

        def rep(m):
            a, b, c, orig = m.groups()
            abc = a or b or c
            if abc:
                return source.get(abc, '')
            if orig:
                return source.get(k)

        for e in (self.base_environ, self.environ):
            for k, v in self.environ.iteritems():
                v = re.sub(r'\$\{(\w+)\}|\$(\w+)|%(\w+)%|(@)', rep, v)
                diff[k] = v

        return diff

    _environ_diff = None

    @property
    def environ_diff(self):
        if self._environ_diff is None:
            self._environ_diff = self._resolve_environ()
            for k, v in sorted(self._environ_diff.iteritems()):
                old_v = os.environ.get(k)
                if old_v is not None:
                    v = v.replace(old_v, '@')
                v = v.replace(self.home.root, '$VEE')
                log.debug('%s %s%s' % (
                    style('setenv', 'blue', bold=True), style('%s=' % k, bold=True), v
                ), verbosity=1)
        return self._environ_diff or {}

    def fresh_environ(self):
        environ = os.environ.copy()
        environ.update(self.environ_diff)
        return environ

    def _set_default_names(self, package=False, build=False, install=False):
        if (package or build or install) and self.package_name is None:
            if self.url:
                # Strip out the scheme.
                name = re.sub(r'^[\w._+-]+:', '', self.url)
                name = re.sub(r':?/+:?', '/', name)
                name = name.strip('/')
                self.package_name = os.path.join(self.type, name)
        if (install or build) and self.install_name is None:
            if self.name and self.revision:
                self.install_name = '%s/%s' % (self.name, self.revision)
            else:
                self.install_name = self.package_name and re.sub(r'(\.(tar|gz|tgz|zip))+$', '', self.package_name)
        if build and self.build_name is None:
            self.build_name = self.install_name and ('%s/%s-%s' % (
                self.install_name,
                datetime.datetime.utcnow().strftime('%y%m%d%H%M%S'),
                os.urandom(4).encode('hex'),
            ))

    def _set_names(self, **kwargs):
        self._set_default_names(**kwargs)

    def _assert_names(self, **kwargs):
        self._set_names(**kwargs)
        for attr, value in kwargs.iteritems():
            if value and not getattr(self, '_%s_name' % attr):
                raise RuntimeError('%s name required' % attr)

    def _assert_paths(self, **kwargs):
        self._set_names(**kwargs)
        for attr, value in kwargs.iteritems():
            if value and not getattr(self, '%s_path' % attr):
                raise RuntimeError('%s path required' % attr)

    @property
    def package_path(self):
        """Where the package is cached."""
        return self.package_name and self.home._abs_path('packages', self.package_name)

    @property
    def build_path(self):
        """Where the package will be built."""
        return self.build_name and self.home._abs_path('builds', self.build_name)

    @property
    def install_path(self):
        """The final location of the built package."""
        return self.install_name and self.home._abs_path('installs', self.install_name)

    def fetch(self):
        """Cache package from remote source; return something representing the package."""

    @property
    def build_path_to_install(self):
        return os.path.join(self.build_path, self.build_subdir or '').rstrip('/')

    @property
    def install_path_from_build(self):
        return os.path.join(self.install_path, self.install_prefix or '').rstrip('/')

    def _clean_build_path(self, makedirs=True):
        if self.build_path and os.path.exists(self.build_path):
            shutil.rmtree(self.build_path)
        if makedirs:
            os.makedirs(self.build_path)

    def extract(self):
        """Extract the package into the (cleaned) build directory."""

        self._set_names(package=True, build=True)

        if not self.package_path:
            return
        if not self.build_path:
            raise RuntimeError('need build path for default Package.extract')

        log.info(style('Extracting to ', 'blue', bold=True) + style(self.build_path, bold=True))

        # Tarballs.
        if re.search(r'(\.tgz|\.tar\.gz)$', self.package_path):
            self._clean_build_path()
            call(['tar', 'xzf', self.package_path], cwd=self.build_path)

        # Zip files (and Python wheels).
        elif re.search(r'(\.zip|\.egg|\.whl)$', self.package_path):
            self._clean_build_path()
            call(['unzip', self.package_path], cwd=self.build_path)

        # Directories.
        elif os.path.isdir(self.package_path):
            self._clean_build_path(makedirs=False)
            if self.hard_link:
                linktree(self.package_path, self.build_path, symlinks=True,
                    ignore=shutil.ignore_patterns('.git'),
                )
            else:
                shutil.copytree(self.package_path, self.build_path, symlinks=True,
                    ignore=shutil.ignore_patterns('.git'),
                )

        else:
            raise ValueError('unknown package type %r' % self.package_path)

    @cached_property
    def builder(self):
        return make_builder(self)

    def build(self):
        """Build the package in the build directory."""
        self._assert_paths(build=True)
        self.builder.build()

    @property
    def installed(self):
        self._assert_paths(install=True)
        return self.install_path and os.path.exists(self.install_path)

    def install(self):
        """Install the build artifact into a final location."""

        self._assert_paths(build=True, install=True)

        if self.installed:
            raise AlreadyInstalled('was already installed at %s' % self.install_path)

        self.builder.install()

        if self.relocate:
            log.info(style('Relocating', 'blue'))
            libs.relocate(self.install_path,
                con=self.home.db.connect(),
                spec=self.relocate + ',SELF',
            )

        # Link into $VEE/opt.
        if self.name:
            opt_link = self.home._abs_path('opt', self.name)
            log.info(style('Linking to opt/%s: ' % self.name, 'blue', bold=True) + style(opt_link, bold=True))
            if os.path.exists(opt_link):
                os.unlink(opt_link)
            makedirs(os.path.dirname(opt_link))
            os.symlink(self.install_path, opt_link)

    def uninstall(self):
        self._set_names(install=True)
        if not self.installed:
            raise RuntimeError('package is not installed')
        log.info(style('Uninstalling ', 'blue', bold=True) + style(self.install_path, bold=True))
        shutil.rmtree(self.install_path)

    def shared_libraries(self, rescan=False):
        self._assert_paths(install=True)
        if not self.installed:
            raise RuntimeError('cannot find libraries if not installed')
        if not self._db_id:
            # I'm not sure if this is a big deal, but I want to see when
            # it is happening.
            log.warning('Finding shared libraries before package is in database.')
        return libs.get_installed_shared_libraries(self.home.db.connect(), self.db_id(), self.install_path, rescan)

    def link(self, env, force=False):
        self._assert_paths(install=True)
        frozen = self.freeze()
        if not force:
            self._assert_unlinked(env, frozen)
        log.info(style('Linking ', 'blue', bold=True) + style(str(frozen), bold=True))
        env.link_directory(self.install_path)
        self._record_link(env)

    def _assert_unlinked(self, env, frozen=None):
        if not self._db_link_id:
            row = self.home.db.execute(
                'SELECT id FROM links WHERE package_id = ? AND environment_id = ?',
                [self.db_id(), env.db_id()]
            ).fetchone()
        if self._db_link_id or row:
            raise AlreadyLinked(str(frozen or self.freeze()), self._db_link_id or row[0])

    def db_id(self):
        if self._db_id is None:
            self._set_names(package=True, build=True, install=True)
            if not self.installed:
                log.warning('%s does not appear to be installed to %s' % (self.name, self.install_path))
                raise ValueError('cannot record requirement that is not installed')
            cur = self.home.db.cursor()
            cur.execute('''
                INSERT INTO packages (abstract_requirement, concrete_requirement,
                                      package_type, build_type, url, name, revision, etag, package_name, build_name,
                                      install_name, package_path, build_path, install_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', [self.abstract_requirement,
                  self.freeze().to_json(),
                  self.type,
                  self.builder.type,
                  self.url,
                  self.name,
                  self.revision,
                  self.etag,
                  self.package_name,
                  self.build_name,
                  self.install_name,
                  self.package_path,
                  self.build_path,
                  self.install_path,
                 ]
            )
            self._db_id = cur.lastrowid
            log.debug('%s added to DB as %d' % (self.name, self._db_id))
        return self._db_id

    def resolve_existing(self, env=None):
        """Check against the database to see if this was already installed."""

        if self._db_id is not None:
            raise ValueError('requirement already in database')


        clauses = ['install_path IS NOT NULL', 'url = ?']
        values  = [self.url]

        for name in ('name', 'revision', 'etag', 'package_name', 'build_name', 'install_name'):
            if getattr(self, name):
                clauses.append('%s = ?' % name)
                values.append(getattr(self, name))

        cur = self.home.db.cursor()
        clause = ' AND '.join(clauses)

        if env:
            values.append(env.db_id())
            cur.execute('''
                SELECT packages.*, links.id as link_id FROM packages
                LEFT OUTER JOIN links ON packages.id = links.package_id
                WHERE %s AND links.environment_id = ?
                ORDER BY links.created_at DESC, packages.created_at DESC
            ''' % clause, values)
        else:
            cur.execute('''
                SELECT packages.*, NULL as link_id FROM packages
                WHERE %s
                ORDER BY packages.created_at DESC
            ''' % clause, values)

        for row in cur:
            if not os.path.exists(row['install_path']):
                log.warning('Found %s (%d) does not exist at %s' % (self.name or row['name'], row['id'], row['install_path']))
                continue
            break
        else:
            return

        log.debug('Found %s (%d) at %s' % (self.name or row['name'], row['id'], row['install_path']))

        # Everything below either already matches or was unset.
        self._db_id = row['id']
        self._db_link_id = row['link_id']
        self.name = row['name']
        self.revision = row['revision']
        self.package_name = row['package_name']
        self.build_name = row['build_name']
        self.install_name = row['install_name']

        if self.package_path != row['package_path']:
            log.warning('Package paths don\'t match:\n  old: %r\n  new: %r' % (row['package_path'], self.package_path))
        if self.build_path != row['build_path']:
            log.warning('Builds paths don\'t match:\n  old: %r\n  new: %r' % (row['build_path'], self.build_path))
        if self.install_path != row['install_path']:
            log.warning('Install paths don\'t match:\n  old: %r\n  new: %r' % (row['install_path'], self.install_path))

        return True

    def _record_link(self, env):
        cur = self.home.db.cursor()
        cur.execute('''INSERT INTO links (package_id, environment_id, abstract_requirement) VALUES (?, ?, ?)''', [
            self.db_id(),
            env.db_id(),
            self.abstract_requirement,
        ])
        self._db_link_id = cur.lastrowid


    def _reinstall_check(self, force):
        if self.installed:
            if force:
                self.uninstall()
            else:
                raise AlreadyInstalled(str(self.freeze()))

    def auto_install(self, force=False):

        self._reinstall_check(force)

        self.fetch()
        self._reinstall_check(force) # We may only know once we have fetched.
    
        self.extract()
        self._reinstall_check(force) # Packages may self-describe.

        self.build()
        self.install()

        # Record it!
        self.db_id()

        # Find shared libs for others.
        self.shared_libraries()




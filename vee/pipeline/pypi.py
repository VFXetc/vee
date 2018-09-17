import datetime
import os
import urlparse
import re
import shutil
import json
import sys

from vee.cli import style, style_note
from vee.pipeline.base import PipelineStep
from vee.utils import makedirs, http_request
from vee import log
from vee.semver import Version, VersionExpr
from vee.pipeline.http import download

PYPI_URL_PATTERN = 'https://pypi.org/pypi/%s/json'


class PyPiTransport(PipelineStep):
    
    factory_priority = 1000

    @classmethod
    def factory(cls, step, pkg):
        if step == 'init' and re.match(r'^pypi[:+]', pkg.url):
            return cls(pkg)

    def get_next(self, step):
        if step in ('fetch', ):
            return self

    def init(self):
        pkg = self.package
        self.name = re.sub(r'^pypi[:+]', '', pkg.url).lower()
        pkg.url = 'pypi:' + self.name

    def _meta(self):
        pkg = self.package
        path = pkg.home._abs_path('packages', 'pypi', self.name, 'meta.json')

        meta = None
        if os.path.exists(path):
            try:
                meta = json.load(open(path, 'rb'))
            except ValueError:
                pass

        if meta is None:

            log.info(style_note('Looking up %s on PyPI' % self.name))
            url = PYPI_URL_PATTERN % self.name
            res = http_request('GET', url)
            body = res.data
            meta = json.loads(body)

            makedirs(os.path.dirname(path))
            with open(path + '.tmp', 'wb') as fh:
                # We re-dump to get indenting.
                fh.write(json.dumps(meta, indent=4, sort_keys=True))
            os.rename(path + '.tmp', path)

        return meta

    def fetch(self):

        pkg = self.package
        meta = self._meta()

        all_releases = [(Version(v), rs) for v, rs in meta['releases'].iteritems()]
        all_releases.sort(reverse=True)

        if pkg.revision:
            expr = VersionExpr(pkg.revision)
            matching_releases = [(v, rs) for v, rs in all_releases if expr.eval(v)]
            log.debug('%s matched %s' % (expr, ','.join(str(v) for v, _ in matching_releases) or 'none'))
        else:
            matching_releases = all_releases

        usable_releases = []
        for version, releases in matching_releases:

            for release in releases:

                if release['packagetype'] == 'sdist':
                    usable_releases.append((version, 0, release))
                    continue

                if release['packagetype'] == 'bdist_wheel':
                    m = re.match(r'^(.+)-([^-]+)-([^-]+)-([^-]+)-([^-]+)\.whl$', release['filename'])
                    if not m:
                        log.warning("Could not parse wheel filename: {}".format(release['filename']))
                    name, version_tag, python_tag, abi_tag, platform_tag = m.groups()

                    if python_tag not in ('py2', 'py27', 'py2.py3'):
                        continue

                    if abi_tag not in ('none', ):
                        continue

                    if platform_tag != 'any':
                        if sys.platform == 'darwin':
                            if not platform_tag.startswith('macos'):
                                continue
                        elif sys.platform.startswith('linux'):
                            if platform_tag not in ('manylinux1_x86_64', ):
                                continue
                        else:
                            continue

                    usable_releases.append((version, 1, release))

        if not usable_releases:
            raise ValueError('no usable release of %s %s on the PyPI;' % (self.name, expr if pkg.revision else '(any version)'))
        usable_releases.sort()

        version, _, release = usable_releases[-1]

        pkg.revision = str(version)
        
        if release.get('md5_digest'):
            pkg.checksum = 'md5:%s' % release['md5_digest']

        pkg.package_name = os.path.join(self.name, os.path.basename(release['url']))
        pkg._assert_paths(package=True)

        if os.path.exists(pkg.package_path):
            log.info(style_note('Already downloaded', release['url']))
            return
        log.info(style_note('Downloading', release['url']))
        download(release['url'], pkg.package_path)



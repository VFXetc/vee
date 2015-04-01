import os
import re
import subprocess

from vee.cli import style
from vee.git import GitRepo, normalize_git_url
from vee.pipeline.base import PipelineStep
from vee.subproc import call
from vee.utils import makedirs, cached_property


class GitTransport(PipelineStep):

    factory_priority = 1000

    @classmethod
    def factory(cls, step, pkg, *args):
        if step == 'init' and re.match(r'^git[:+]', pkg.url):
            return cls(pkg, *args)

    def get_next(self, step):
        if step == 'fetch':
            return self

    def init(self):
        pkg = self.package
        pkg.url = normalize_git_url(pkg.url, prefix=True) or pkg.url
    
    def fetch(self):
        pkg = self.package
        pkg._assert_paths(package=True)
        self.repo = GitRepo(work_tree=pkg.package_path, remote_url=re.sub(r'^git[:\+]', '', pkg.url))
        self.repo.clone_if_not_exists()
        self.repo.checkout(pkg.revision or 'HEAD', fetch=True)
        pkg.revision = self.repo.head[:8]

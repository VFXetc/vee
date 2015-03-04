import re

from vee.commands.main import command, argument
from vee.utils import style, style_error, style_note
from vee.git import GitRepo, normalize_git_url
from vee.exceptions import CliException


@command(
    argument('--bake-heads', action='store_true'),
    argument('package', nargs='?', default='.'),
)
def add(args):

    home = args.assert_home()


    if args.bake_heads:
        req_repo = home.get_repo()
        baked_any = False
        for req in req_repo.iter_git_requirements(home):
            pkg = req.package
            pkg.resolve_existing()
            if pkg.installed and re.match(r'^[0-9a-f]{8}$', pkg.revision) and req.revision != pkg.revision:
                req.revision = pkg.revision
                req.force_fetch = False
                print style_note('Baked', str(req))
                baked_any = True
        if baked_any:
            req_repo.dump()
        else:
            print style_note('No changes.')
        return


    row = home.get_development_record(args.package)

    if not row:
        raise CliException('No development package %r' % args.package)

    pkg_repo = GitRepo(row['path'])

    # Get the normalized origin.
    pkg_url = pkg_repo.remotes().get('origin')
    if not pkg_url:
        raise CliException('%s does not have an origin' % row['path'])
    pkg_url = normalize_git_url(pkg_url)
    if not pkg_url:
        raise CliException('%s does not appear to be a git url' % pkg_url)

    req_repo = home.get_repo()
    for req in req_repo.iter_git_requirements(home):
        req_url = normalize_git_url(req.url)
        if req_url == pkg_url:
            break
    else:
        raise CliException('could not find matching package')

    if req.revision == pkg_repo.head[:8]:
        print style_note('No change to', str(req))
    else:
        req.force_fetch = False
        req.revision = pkg_repo.head[:8]
        print style_note('Updated', str(req))
        req_repo.dump()

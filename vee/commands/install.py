from vee.commands.main import command, argument
from vee.requirement import Requirement
from vee.utils import style


@command(
    argument('--force', action='store_true', help='force install over old package'),
    argument('package', nargs='...'),
    help='install a package',
    usage='vee install [--force] PACKAGE [OPTIONS]',
)
def install(args):
    args.assert_home()
    req = Requirement(args.package, home=args.home)

    if not args.force:
        req.package.resolve_existing()

    req.install(force=args.force)
    

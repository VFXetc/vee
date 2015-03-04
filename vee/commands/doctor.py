from vee.commands.main import command, argument
from vee.utils import style, style_note, style_warning


@command(
    argument('--ping', action='store_true', help='print "pong"'),
    help='perform a self-check',
)
def doctor(args):

    if args.ping:
        print 'pong'
        return

    home = args.assert_home()

    print style_note('Home:', home.root)

    try:
        repo = home.get_repo()
    except ValueError:
        print style_warning('No default repo.', 'Use `vee repo --add URL`.')
        return

    print style_note('Default repo:', repo.name, repo.remote_url)

    print style_warning('Doctor is incomplete.', 'There are many things we aren\'t testing yet.')
    print style('OK', 'green', bold=True)

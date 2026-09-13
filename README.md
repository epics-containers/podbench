# Podbench

Debug running Kubernetes applications and iterate on their source without building
an image for every edit. Podbench provides an ephemeral debugging container
(a **seat**), SSH access, and VS Code debug launchers. Hotfix adds a persistent
source checkout and an application restart loop.

Start with the [documentation](https://epics-containers.github.io/podbench/main/):

- [Set up for P47](docs/tutorials/installation.md)
- [Debug a running IOC with attach](docs/tutorials/attach.md)
- [Edit and debug BlueAPI with hotfix](docs/tutorials/hotfix.md)
- [Work remotely through an SSH tunnel](docs/how-to/remote-work.md)

At Diamond Light Source:

```bash
module load ec/p47
module load uv
uv tool install git+https://github.com/epics-containers/podbench@main
podbench doctor --fix
```

This is a prototype. Debugging pauses live application processes; agree a suitable
session with the beamline team.

To build or preview the docs from a checkout:

```bash
uv run --locked tox -e docs
uv run --locked tox -e docs-autobuild
```

The HTML build is in `build/html`. See [building the docs](docs/how-to/build-docs.md)
for the Copier configuration and publishing workflow.

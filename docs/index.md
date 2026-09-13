# Podbench

Debug an application where it is running. **Attach** adds a debugging seat to a
Kubernetes pod; **hotfix** lets you edit a persistent source checkout and restart
the application without rebuilding its image.

These guides use Diamond Light Source's P47 beamline: the PMAC IOC
`bl47p-mo-ioc-01-0` and BlueAPI `p47-blueapi-0`. Short asides explain what to
substitute outside DLS.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Debug a running IOC
:link: tutorials/attach
:link-type: doc
Connect to P47, select a process, and inspect it in GDB or VS Code.
:::

:::{grid-item-card} Edit and debug BlueAPI
:link: tutorials/hotfix
:link-type: doc
Change Python source on the hotfix claim, restart, and hit a breakpoint.
:::

::::

Start with [workstation setup](tutorials/installation.md).
For access from home, see [remote work](how-to/remote-work.md).

```{toctree}
:maxdepth: 2

tutorials
how-to
explanations
reference
```

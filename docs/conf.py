"""Configuration file for the Sphinx documentation builder.

This file only contains a selection of the most common options. For a full
list see the documentation:
https://www.sphinx-doc.org/en/master/usage/configuration.html
"""

import sys
from pathlib import Path
from subprocess import check_output

root = Path(__file__).absolute().parent.parent
sys.path.insert(0, str(root / "src"))

import podbench  # noqa: E402

# -- General configuration ------------------------------------------------

# General information about the project.
project = "podbench"

# The full version, including alpha/beta/rc tags.
release = podbench.__version__

# The short X.Y version.
if "+" in release:
    # Not on a tag, use branch name
    git_branch = check_output("git branch --show-current".split(), cwd=root)
    version = git_branch.decode().strip()
else:
    version = release

extensions = [
    # Render the architecture diagram
    "sphinx.ext.graphviz",
    # Add a copy button to each code block
    "sphinx_copybutton",
    # For the card element
    "sphinx_design",
    # So we can write markdown files
    "myst_parser",
]

# So we can use the ::: syntax
myst_enable_extensions = ["colon_fence"]

# If true, Sphinx will warn about all references where the target cannot
# be found.
nitpicky = True

# Output graphviz directive produced images in a scalable format
graphviz_output_format = "svg"

# The name of a reST role (builtin or Sphinx extension) to use as the default
# role, that is, for text marked up `like this`
default_role = "any"

# The master toctree document.
master_doc = "index"

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# These patterns also affect html_static_path and html_extra_path
exclude_patterns = ["_build"]

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = "sphinx"

# Ignore localhost links for periodic check that links in docs are valid
linkcheck_ignore = [r"http://(?:localhost|127\.0\.0\.1):\d+/"]

# Set copy-button to ignore python and bash prompts
# https://sphinx-copybutton.readthedocs.io/en/latest/use.html#using-regexp-prompt-identifiers
copybutton_prompt_text = r">>> |\.\.\. |\$ |In \[\d*\]: | {2,5}\.\.\.: | {5,8}: "
copybutton_prompt_is_regexp = True

# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "pydata_sphinx_theme"
github_repo = "podbench"
github_user = "epics-containers"
switcher_json = f"https://{github_user}.github.io/{github_repo}/switcher.json"
# The switcher is not checked: the docs workflow publishes it after the build.
html_theme_options = {
    "logo": {
        "text": project,
    },
    "use_edit_page_button": True,
    "github_url": f"https://github.com/{github_user}/{github_repo}",
    "icon_links": [
        {
            "name": "PyPI",
            "url": f"https://pypi.org/project/{project}",
            "icon": "fas fa-cube",
        }
    ],
    "switcher": {
        "json_url": switcher_json,
        "version_match": version,
    },
    "check_switcher": False,
    "navbar_end": ["theme-switcher", "icon-links", "version-switcher"],
    "navigation_with_keys": False,
}

# A dictionary of values to pass into the template engine’s context for all pages
html_context = {
    "github_user": github_user,
    "github_repo": github_repo,
    "github_version": version,
    "doc_path": "docs",
}

# If true, "Created using Sphinx" is shown in the HTML footer. Default is True.
html_show_sphinx = False

# If true, "(C) Copyright ..." is shown in the HTML footer. Default is True.
html_show_copyright = False

# Logo
html_logo = "images/dls-logo.svg"
html_favicon = html_logo

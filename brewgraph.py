#!/usr/bin/env python3
"""Inspect installed Homebrew packages and render their dependency graph
as an interactive HTML page (vis-network via CDN, no extra installs)."""

import json
import os
import subprocess
import sys
import webbrowser

OUTPUT = "brew-deps.html"


def get_deps():
    # Redirect brew's cache and skip bootsnap: avoids EPERM failures in
    # sandboxed/restricted environments, harmless otherwise.
    env = dict(
        os.environ,
        HOMEBREW_NO_BOOTSNAP="1",
        HOMEBREW_NO_AUTO_UPDATE="1",
        HOMEBREW_CACHE="/tmp/brewgraph-cache",
    )
    result = subprocess.run(
        ["brew", "deps", "--installed"],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        sys.exit(f"brew deps failed:\n{result.stderr}")
    deps = {}
    for line in result.stdout.splitlines():
        name, sep, rest = line.partition(":")
        if sep:
            deps[name.strip()] = rest.split()
    return deps


def build_graph(deps):
    depended_on = {d for ds in deps.values() for d in ds}
    nodes = []
    for name in sorted(set(deps) | depended_on):
        is_root = name not in depended_on
        nodes.append({
            "id": name,
            "label": name,
            "color": "#4c9aff" if is_root else "#b0bec5",
            "shape": "dot",
            "size": 14 if is_root else 9,
        })
    edges = [{"from": pkg, "to": dep, "arrows": "to"}
             for pkg, ds in deps.items() for dep in ds]
    return nodes, edges


TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Homebrew dependency graph</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  body {{ margin: 0; font-family: -apple-system, sans-serif; }}
  #header {{ padding: 8px 16px; background: #263238; color: #eceff1; }}
  #header small {{ color: #90a4ae; margin-left: 12px; }}
  #graph {{ width: 100vw; height: calc(100vh - 40px); }}
</style>
</head>
<body>
<div id="header"><b>Homebrew dependency graph</b>
  <small>{n_nodes} packages, {n_edges} dependencies &mdash;
  blue = top-level (nothing depends on it), grey = dependency.
  Click a node to highlight its dependencies.</small>
</div>
<div id="graph"></div>
<script>
const nodes = new vis.DataSet({nodes});
const edges = new vis.DataSet({edges});
const network = new vis.Network(
  document.getElementById("graph"),
  {{ nodes, edges }},
  {{
    physics: {{
      solver: "forceAtlas2Based",
      forceAtlas2Based: {{ gravitationalConstant: -40, springLength: 60 }},
      stabilization: {{ iterations: 200 }},
    }},
    interaction: {{ hover: true }},
  }}
);
// On click, dim everything except the selected node and its neighbourhood.
const baseColors = {{}};
nodes.forEach(n => baseColors[n.id] = n.color);
network.on("click", params => {{
  const sel = params.nodes[0];
  nodes.forEach(n => {{
    const keep = !sel || n.id === sel ||
      network.getConnectedNodes(sel).includes(n.id);
    nodes.update({{ id: n.id, color: keep ? baseColors[n.id] : "#eceff1" }});
  }});
}});
</script>
</body>
</html>
"""


def main():
    deps = get_deps()
    nodes, edges = build_graph(deps)
    html = TEMPLATE.format(
        nodes=json.dumps(nodes), edges=json.dumps(edges),
        n_nodes=len(nodes), n_edges=len(edges),
    )
    with open(OUTPUT, "w") as f:
        f.write(html)
    print(f"Wrote {OUTPUT}: {len(nodes)} packages, {len(edges)} dependency edges")
    if "--no-open" not in sys.argv:
        webbrowser.open("file://" + os.path.abspath(OUTPUT))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Inspect installed Homebrew packages and render their dependency graph
as an interactive HTML page (vis-network via CDN, no extra installs)."""

import json
import os
import subprocess
import sys
import webbrowser

OUTPUT = "brew-deps.html"


def get_deps(no_casks=False):
    # Redirect brew's cache and skip bootsnap: avoids EPERM failures in
    # sandboxed/restricted environments, harmless otherwise.
    env = dict(
        os.environ,
        HOMEBREW_NO_BOOTSNAP="1",
        HOMEBREW_NO_AUTO_UPDATE="1",
        HOMEBREW_CACHE="/tmp/brewgraph-cache",
    )
    cmd = ["brew", "deps", "--installed"]
    if no_casks:
        cmd.append("--formula")
    result = subprocess.run(
        cmd,
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


def get_casks(no_casks=False):
    if no_casks:
        return set()
    result = subprocess.run(["brew", "list", "--cask", "-1"], capture_output=True, text=True)
    if result.returncode != 0:
        return set()
    return set(result.stdout.split())


def get_sizes(names):
    """Installed on-disk size (KB) for each package, via Cellar/Caskroom dirs."""
    cellar = subprocess.run(["brew", "--cellar"], capture_output=True, text=True).stdout.strip()
    caskroom = subprocess.run(["brew", "--caskroom"], capture_output=True, text=True).stdout.strip()

    ordered_names, ordered_paths = [], []
    for name in names:
        short = name.split("/")[-1]
        for base in (cellar, caskroom):
            if base:
                path = os.path.join(base, short)
                if os.path.isdir(path):
                    ordered_names.append(name)
                    ordered_paths.append(path)
                    break

    sizes_kb = {}
    if ordered_paths:
        # -s summarizes each argument to one line; du preserves argument order.
        result = subprocess.run(["du", "-sk", *ordered_paths], capture_output=True, text=True)
        for name, line in zip(ordered_names, result.stdout.splitlines()):
            kb_str = line.split("\t")[0]
            if kb_str.isdigit():
                sizes_kb[name] = int(kb_str)
    return sizes_kb


def closure(name, deps):
    """A package and all its transitive dependencies."""
    seen = set()
    stack = [name]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(deps.get(n, []))
    return seen


def build_graph(deps, casks):
    depended_on = {d for ds in deps.values() for d in ds}
    core = sorted(set(deps) - depended_on - casks)
    cask_names = sorted(set(deps) & casks)
    nodes = []
    for name in sorted(set(deps) | depended_on):
        is_core = name not in depended_on
        if name in casks:
            color = "#43a047"
        elif is_core:
            color = "#e53935"
        else:
            color = "#4c9aff"
        nodes.append({
            "id": name,
            "label": name,
            "color": color,
            "shape": "dot",
            "size": 14 if is_core else 9,
        })
    edges = [{"from": pkg, "to": dep, "arrows": "to"}
             for pkg, ds in deps.items() for dep in ds]
    return nodes, edges, core, cask_names


def build_sizes_table(names, deps, sizes_kb):
    rows = []
    for name in names:
        deps_closure = closure(name, deps) - {name}
        own_kb = sizes_kb.get(name, 0)
        total_kb = own_kb + sum(sizes_kb.get(d, 0) for d in deps_closure)
        rows.append({
            "name": name,
            "own_mb": round(own_kb / 1024, 1),
            "total_mb": round(total_kb / 1024, 1),
            "n_deps": len(deps_closure),
        })
    rows.sort(key=lambda r: r["total_mb"], reverse=True)
    return rows


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
  #tabs {{ display: flex; background: #37474f; }}
  #tabs button {{
    background: none; border: none; color: #cfd8dc; padding: 10px 20px;
    font-size: 14px; cursor: pointer; border-bottom: 3px solid transparent;
  }}
  #tabs button:hover {{ color: #fff; }}
  #tabs button.active {{ color: #fff; border-bottom-color: #e53935; }}
  #graph {{ width: 100vw; height: calc(100vh - 78px); }}
  .panel {{ width: 100vw; height: calc(100vh - 78px); overflow: auto; display: none; box-sizing: border-box; padding: 16px; }}
  .panel.active {{ display: block; }}
  #graph.hidden {{ display: none; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 800px; }}
  th, td {{ text-align: left; padding: 6px 12px; border-bottom: 1px solid #eceff1; }}
  th {{ background: #eceff1; position: sticky; top: 0; }}
  tr:hover {{ background: #f5f5f5; }}
  td.num, th.num {{ text-align: right; }}
  caption {{ text-align: left; color: #607d8b; padding: 8px 0; font-size: 13px; caption-side: top; }}
</style>
</head>
<body>
<div id="header"><b>Homebrew dependency graph</b>
  <small>{n_nodes} packages, {n_edges} dependencies &mdash;
  red = core formula (top-level, nothing depends on it), blue = formula dependency,
  green = cask. Click a node to highlight its dependencies.</small>
</div>
<div id="tabs">
  <button id="tab-graph" class="active">Graph</button>
  <button id="tab-core">Core packages ({n_core})</button>
  <button id="tab-casks">Casks ({n_casks})</button>
</div>
<div id="graph"></div>
<div id="core" class="panel">
  <table>
    <caption>Estimated on-disk size per core (top-level) formula, own vs. including
    all transitive dependencies. Sizes are from Cellar/Caskroom directories; shared
    dependencies are counted under every core package that uses them, so totals
    overlap and don't sum to actual disk usage.</caption>
    <thead>
      <tr><th>Package</th><th class="num">Own size (MB)</th>
      <th class="num">Incl. dependencies (MB)</th><th class="num"># deps</th></tr>
    </thead>
    <tbody>{core_rows}</tbody>
  </table>
</div>
<div id="casks" class="panel">
  <table>
    <caption>Estimated on-disk size per installed cask, own vs. including
    any formula dependencies it pulls in.</caption>
    <thead>
      <tr><th>Cask</th><th class="num">Own size (MB)</th>
      <th class="num">Incl. dependencies (MB)</th><th class="num"># deps</th></tr>
    </thead>
    <tbody>{cask_rows}</tbody>
  </table>
</div>
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

const tabs = {{
  graph: {{ btn: document.getElementById("tab-graph"), panel: document.getElementById("graph") }},
  core: {{ btn: document.getElementById("tab-core"), panel: document.getElementById("core") }},
  casks: {{ btn: document.getElementById("tab-casks"), panel: document.getElementById("casks") }},
}};
function showTab(name) {{
  for (const key in tabs) {{
    const isActive = key === name;
    tabs[key].btn.classList.toggle("active", isActive);
    if (key === "graph") {{
      tabs[key].panel.classList.toggle("hidden", !isActive);
    }} else {{
      tabs[key].panel.classList.toggle("active", isActive);
    }}
  }}
  if (name === "graph") network.fit();
}}
tabs.graph.btn.addEventListener("click", () => showTab("graph"));
tabs.core.btn.addEventListener("click", () => showTab("core"));
tabs.casks.btn.addEventListener("click", () => showTab("casks"));
</script>
</body>
</html>
"""


def render_size_rows(rows):
    return "".join(
        f"<tr><td>{r['name']}</td><td class='num'>{r['own_mb']}</td>"
        f"<td class='num'>{r['total_mb']}</td><td class='num'>{r['n_deps']}</td></tr>"
        for r in rows
    )


def main():
    no_casks = "--no-casks" in sys.argv
    deps = get_deps(no_casks=no_casks)
    casks = get_casks(no_casks=no_casks)
    nodes, edges, core, cask_names = build_graph(deps, casks)
    sizes_kb = get_sizes(set(deps) | {d for ds in deps.values() for d in ds})
    core_rows = build_sizes_table(core, deps, sizes_kb)
    cask_rows = build_sizes_table(cask_names, deps, sizes_kb)
    html = TEMPLATE.format(
        nodes=json.dumps(nodes), edges=json.dumps(edges),
        n_nodes=len(nodes), n_edges=len(edges),
        n_core=len(core), n_casks=len(cask_names),
        core_rows=render_size_rows(core_rows),
        cask_rows=render_size_rows(cask_rows),
    )
    with open(OUTPUT, "w") as f:
        f.write(html)
    print(f"Wrote {OUTPUT}: {len(nodes)} packages, {len(edges)} dependency edges, "
          f"{len(core)} core packages, {len(cask_names)} casks")
    if "--no-open" not in sys.argv:
        webbrowser.open("file://" + os.path.abspath(OUTPUT))


if __name__ == "__main__":
    main()

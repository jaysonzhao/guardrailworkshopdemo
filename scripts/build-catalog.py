"""Build the participant appendix from the same policies consumed by the CLI and NeMo."""
from html import escape
from pathlib import Path
from rulebook import catalog

ROOT = Path(__file__).resolve().parents[1]
PARTIAL = ROOT / "content/en/modules/ROOT/partials/rule-catalog.adoc"


def command(label, operation, key):
    return (f'<div class="rule-command"><span>{label}</span><div class="listingblock execute"><div class="content">'
            f'<pre class="highlight"><code class="language-bash" data-lang="bash">./grlab catalog {operation} {key}</code>'
            '</pre></div></div></div>')


def build():
    categories, rules = catalog()
    out = ['++++', '<section id="rule-browser" aria-label="Workshop rule catalog">', '<div id="rule-filters" hidden>',
           '<label>Category<select id="rule-category"><option value="">All categories</option>']
    for key, title in categories.items():
        out.append(f'<option value="{key}">{escape(title)}</option>')
    out += ['</select></label>', '<label>Rule<select id="rule-id"><option value="">All rules in category</option></select></label>',
            '<button id="rule-reset" type="button">Clear filters</button></div>',
            '<p id="rule-count" role="status" aria-live="polite"></p>']
    for cat, title in categories.items():
        out.append(f'<section class="rule-group" data-category="{cat}"><h3>{escape(title)}</h3>')
        for key, row in rules.items():
            if row["category"] != cat:
                continue
            name = escape(row["title"])
            engine = "Local · Module 2 or 3" if row["engine"] == "local" else "Nemotron · Module 3"
            state = "Enabled by default" if row["default"] else "Opt in"
            phase = {"both":"Input and output","input":"Input","output":"Output"}[row["phase"]]
            out += [f'<details class="rule-card" data-rule="{key}" data-title="{name}" id="rule-{key}">',
                    f'<summary><span>{name}</span> <code>{key}</code></summary>',
                    f'<p class="rule-meta">{phase} · {engine} · {state}</p>', f'<p>{escape(row["explanation"])}</p>', '<dl>']
            if "context" in row:
                out.append(f'<dt>Customer context</dt><dd>{escape(row["context"])}</dd>')
            label = "Candidate reply" if row["phase"]=="output" else "Customer message"
            action = {"block":"Block","rewrite":"Rewrite the link","handoff":"Return the human-help disclosure"}[row["action"]]
            out += [f'<dt>{label}</dt><dd>{escape(row["sample"])}</dd>', f'<dt>Expected outcome</dt><dd>{action}</dd>',
                    f'<dt>Allowed control</dt><dd>{escape(row["control"])}</dd>', '</dl>']
            out += [command(label, op, key) for label, op in
                    [("Rehearse locally","sample"),("Stage this rule","add"),("Check the deployed example","test"),("Stage removal","remove")]]
            out.append('</details>')
        out.append('</section>')
    out += ['</section>', '++++', '']
    PARTIAL.parent.mkdir(parents=True, exist_ok=True)
    PARTIAL.write_text("\n".join(out))

if __name__ == "__main__":
    build()

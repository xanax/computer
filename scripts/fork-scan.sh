#!/usr/bin/env bash
# Sibling-fork scanner: find forks of upstream that have work we do not.
#
#   scripts/fork-scan.sh            print a report, rewrite notes/fork-scan-report.md
#   MAX_AGE_DAYS=90 scripts/fork-scan.sh
#
# Where scripts/fork-status.sh asks "is our patch still ours?", this asks the
# opposite question: "did somebody else build something worth stealing?" It
# enumerates the forks of upstream, asks GitHub for a cross-fork compare against
# each one, and reports the forks that are ahead of upstream with their commit
# subjects and the top-level areas they touch.
#
# Read-only against the repo: no fetch, no merge, no writes outside notes/.
# Exit status is always 0 unless the scan itself could not happen, so a schedule
# on this never looks like a code failure.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

UPSTREAM=${UPSTREAM:-open-webui/computer}
UP_OWNER=${UPSTREAM%%/*}   # compare refs take OWNER:BRANCH, not OWNER/REPO:BRANCH
SELF=${SELF:-xanax/computer}
BASE=${BASE:-main}
MAX_AGE_DAYS=${MAX_AGE_DAYS:-240}
SUBJECTS_SHOWN=${SUBJECTS_SHOWN:-20}
REPORT=notes/fork-scan-report.md
STATE=notes/fork-scan-state.tsv
SCAN=notes/.fork-scan.tsv

command -v gh >/dev/null || { echo "error: gh not installed" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: gh is not authenticated (gh auth login)" >&2; exit 1; }

NOW=$(date -u '+%Y-%m-%d %H:%M UTC')
TODAY=$(date -u '+%Y-%m-%d')
CUTOFF=$(date -u -d "-${MAX_AGE_DAYS} days" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || CUTOFF=1970-01-01T00:00:00Z

USHA=$(gh api "repos/$UPSTREAM/commits/$BASE" --jq '.sha[0:7]' 2>/dev/null) || {
	echo "error: cannot read $UPSTREAM" >&2; exit 1; }
USDATE=$(gh api "repos/$UPSTREAM/commits/$BASE" --jq '.commit.committer.date[0:10]' 2>/dev/null)
USUBJ=$(gh api "repos/$UPSTREAM/commits/$BASE" --jq '.commit.message | split("\n")[0]' 2>/dev/null)
UPUSHED=$(gh api "repos/$UPSTREAM" --jq '.pushed_at' 2>/dev/null)

echo "scanning forks of $UPSTREAM (base $USHA, last push $UPUSHED)..." >&2

# Internal records are joined with U+001F, not tab. Bash treats tab (and space)
# as IFS *whitespace*: a run of them delimits once, so a record with an empty
# field silently shifts every field after it — a fork whose compare carried no
# `files` array reported its head sha as its "areas" and its commit count as its
# head sha. U+001F is not IFS whitespace, so empty fields survive. The files
# written to notes/ still use tabs; only the shell's own reads need this.
US=$(printf '\037')

# full_name | default_branch | pushed_at, newest first. `--paginate` with `--jq`
# prints the projection once per page, which is what we want.
gh api "repos/$UPSTREAM/forks?per_page=100&sort=newest" --paginate \
	--jq '.[] | [.full_name, (.default_branch // "main"), .pushed_at] | join("\u001f")' >"$SCAN.raw" 2>/dev/null \
	|| { echo "error: cannot list forks" >&2; exit 1; }

: >"$SCAN"
scanned=0
while IFS=$US read -r fork branch pushed; do
	[ -n "${fork:-}" ] || continue
	[ "$fork" = "$SELF" ] && continue
	# A fork we have never heard of that has not moved in MAX_AGE_DAYS is very
	# unlikely to hold a fresh idea and costs one API call to rule out.
	if [[ "$pushed" < "$CUTOFF" ]]; then continue; fi
	scanned=$((scanned + 1))

	# One request per fork: `compare` carries ahead_by/behind_by, the commit
	# subjects, and (capped at 300) the changed filenames. The base ref is
	# `owner:branch` — writing `$UPSTREAM:$BASE` yields `open-webui/computer:main`
	# and GitHub answers 404 for every fork.
	url="repos/$UPSTREAM/compare/$UP_OWNER:$BASE...${fork%%/*}:$branch"
	# 2>&1: on a failure gh writes the API's own reason ("No common ancestor…",
	# "Not Found") and keeping it beats guessing at the cause in the report.
	res=$(gh api "$url" \
		--jq '[(.ahead_by|tostring), (.behind_by|tostring),
		       ([.commits[]?.commit.message | split("\n")[0] | gsub("[\\t\\n]"; " ")] | join(" ;; ")),
		       ([.files[]?.filename | split("/")[0] | select(length < 24)] | unique | join(",")),
		       (.commits[-1].sha // ""), ((.commits|length)|tostring)] | join("\u001f")' 2>&1)
	if [ -z "$res" ] || [[ "$res" != *$US* ]]; then
		# One retry: a rate-limit blip on a single fork should not park it in
		# the "Unscanned" list for a whole month.
		sleep 3
		res=$(gh api "$url" \
			--jq '[(.ahead_by|tostring), (.behind_by|tostring),
			       ([.commits[]?.commit.message | split("\n")[0] | gsub("[\\t\\n]"; " ")] | join(" ;; ")),
			       ([.files[]?.filename | split("/")[0] | select(length < 24)] | unique | join(",")),
			       (.commits[-1].sha // ""), ((.commits|length)|tostring)] | join("\u001f")' 2>&1)
	fi
	if [ -z "$res" ] || [[ "$res" != *$US* ]]; then
		reason=$(printf '%s' "$res" | tr '\t\n' '  ' | sed -n 's/.*"message":"\([^"]*\)".*/\1/p;t;s/.*gh: \(.*\) (HTTP.*/\1/p' | head -1)
		printf '%s\tERR\tERR\t%s\t%s\t\t\t\n' "$fork" "$pushed" "${reason:-compare failed}" >>"$SCAN"
		echo "  $fork: ${reason:-compare failed}" >&2
		continue
	fi
	IFS=$US read -r ahead behind subjects dirs headsha ncommits <<<"$res"
	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
		"$fork" "$ahead" "$behind" "$pushed" "$dirs" "$ncommits" "$headsha" "$subjects" >>"$SCAN"
	# Only chatter about the ones that matter; 80 "behind=0 ahead=0" lines is noise.
	[ "$ahead" != "0" ] && echo "  $fork: $ahead ahead, $behind behind" >&2
done <"$SCAN.raw"

SCAN="$SCAN" STATE="$STATE" REPORT="$REPORT" \
UPSTREAM="$UPSTREAM" UP_OWNER="$UP_OWNER" SELF="$SELF" USHA="$USHA" \
USDATE="$USDATE" USUBJ="$USUBJ" BASE="$BASE" \
UPUSHED="$UPUSHED" NOW="$NOW" TODAY="$TODAY" MAX_AGE_DAYS="$MAX_AGE_DAYS" \
SUBJECTS_SHOWN="$SUBJECTS_SHOWN" SCANNED="$scanned" python3 - <<'PY'
import os, re, sys

env = os.environ
scan, state, report = env["SCAN"], env["STATE"], env["REPORT"]

def noise(subject):
    """Commits that carry no idea worth importing: syncing upstream, chores,
    version bumps, dependency bots, CI-file tinkering, housekeeping typos."""
    s = subject.lower().strip()
    return bool(re.match(
        r"(merge |revert \"merge|sync |update (changelog|version|readme|deps)|"
        r"chore|bump |dependabot|renovate|whitespace|lint|typo|wip\b|"
        r"fix(ed)? (a )?typo|"
        r"(add|create|update|delete|rename) [\w.-]+\.(ya?ml|json|txt)$|"
        r"update [\w-]+ )", s))

forks = []
with open(scan, encoding="utf-8") as fh:
    for line in fh:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 8:
            continue
        name, ahead, behind, pushed, dirs, ncommits, headsha, subjects = parts[:8]
        # Compare repeats a subject when a fork carries the same commit twice
        # (cherry-pick plus original); collapsing keeps the list readable and
        # keeps the substantive count honest.
        subs = list(dict.fromkeys(s for s in subjects.split(" ;; ") if s))
        subst = [s for s in subs if not noise(s)]
        forks.append(dict(name=name, err=(ahead == "ERR"), ahead=ahead, behind=behind,
                          pushed=pushed, dirs=dirs, ncommits=ncommits, headsha=headsha,
                          subs=subs, subst=subst, reason=dirs))

prev = {}
if os.path.exists(state):
    with open(state, encoding="utf-8") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3:
                prev[p[0]] = p[2]

cands = [f for f in forks if not f["err"] and f["ahead"].isdigit() and int(f["ahead"]) > 0]
# Rank by substantive commits: a fork 260 commits ahead that is 250 syncs is
# less interesting than one 4 commits ahead with 4 real features.
cands.sort(key=lambda f: (len(f["subst"]), int(f["ahead"]), f["pushed"]), reverse=True)
new = [f for f in cands if prev.get(f["name"]) not in (None, f["headsha"])]

out = []
out.append("# Sibling fork scan")
out.append("")
out.append(f"_Generated by `scripts/fork-scan.sh` on {env['NOW']}._")
out.append("")
out.append("Which forks of `%s` have built something we do not have, and is it worth importing?" % env["UPSTREAM"])
out.append("Read-only: this scans GitHub, it does not fetch or merge anything.")
out.append("")
out.append("| | |")
out.append("| --- | --- |")
out.append("| Upstream | `%s` @ `%s` (%s) — \"%s\" |" % (env["UPSTREAM"], env["USHA"], env["USDATE"], env["USUBJ"]))
out.append("| Forks scanned | %s (last push within %s days) |" % (env["SCANNED"], env["MAX_AGE_DAYS"]))
out.append("| Forks with commits we lack | %s |" % len(cands))
out.append("| New since the previous scan | %s |" % len(new))
out.append("")
if new:
    out.append("## New since the previous scan")
    out.append("")
    for f in new:
        out.append("- `%s` — %s commits ahead, last push %s" % (f["name"], f["ahead"], f["pushed"][:10]))
    out.append("")

out.append("## Candidates")
out.append("")
out.append("Ranked by substantive commits (sync/chore/merge commits excluded), then by distance ahead.")
out.append("")
out.append("| fork | ahead | behind | substantive | last push | areas |")
out.append("| --- | --- | --- | --- | --- | --- |")
for f in cands:
    out.append("| `%s` | %s | %s | %s | %s | %s |" % (
        f["name"], f["ahead"], f["behind"], len(f["subst"]), f["pushed"][:10], f["dirs"] or "—"))
out.append("")

shown = [f for f in cands if f["subst"]][:12]
if shown:
    out.append("## Commit subjects")
    out.append("")
    for f in shown:
        out.append("### `%s` — %s ahead, %s substantive" % (f["name"], f["ahead"], len(f["subst"])))
        out.append("")
        out.append("Last push %s. Areas: %s" % (f["pushed"][:10], f["dirs"] or "—"))
        out.append("")
        for s in f["subst"][:int(env["SUBJECTS_SHOWN"])]:
            out.append("- %s" % s)
        if len(f["subst"]) > int(env["SUBJECTS_SHOWN"]):
            out.append("- _…%d more_" % (len(f["subst"]) - int(env["SUBJECTS_SHOWN"])))
        out.append("")

errs = [f for f in forks if f["err"]]
if errs:
    out.append("## Unscanned")
    out.append("")
    out.append("GitHub refused the compare (rewritten history with no common ancestor, deleted")
    out.append("branch, private fork). ")
    out.append("")
    for f in errs:
        out.append("- `%s` — %s" % (f["name"], f["reason"] or "compare failed"))
    out.append("")

out.append("## How to look at one")
out.append("")
out.append("```bash")
out.append("gh api repos/%s/compare/%s:%s...OWNER:%s --jq '.commits[].commit.message'  # subjects"
           % (env["UPSTREAM"], env["UP_OWNER"], env["BASE"], env["BASE"]))
out.append("git fetch https://github.com/OWNER/computer main:refs/remotes/scan/OWNER  # then git log/diff")
out.append("```")
out.append("")
out.append("Then judge it the way the fork's own ledger does: a sibling's feature is only")
out.append("worth importing if it is smaller than our version of the problem, or if it fixes")
out.append("something we have a probe for in `scripts/fork-probes.tsv`.")
out.append("")
out.append("Legend: `ahead` = commits the fork has that upstream lacks (includes commits the")
out.append("fork kept from an older upstream). `substantive` = distinct subjects surviving the")
out.append("noise filter (`Merge`/`Sync`/`chore`/`Bump`/`dependabot`/`typo`/CI-file edits). File lists are")
out.append("capped by GitHub's `compare` endpoint at 300 files and 250 commits.")

with open(report, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")

with open(state, "w", encoding="utf-8") as fh:
    for f in cands:
        fh.write("%s\t%s\t%s\n" % (f["name"], f["ahead"], f["headsha"]))

print("\n".join(out))
PY

rm -f "$SCAN" "$SCAN.raw"

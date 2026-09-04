# This is a separate project, parked here temporarily

`deadweight` is its own tool, not part of whyskill. It is stored in this
directory only because the session that wrote it could not create a GitHub
repository (the GitHub App has no repo-creation permission) and the container it
was built in is ephemeral.

## To give it its own repo

```bash
# 1. Create an empty repo named `deadweight` on GitHub (no README, no license).
# 2. Then, from the root of this checkout:
cp -r _new-repo-deadweight /tmp/deadweight
cd /tmp/deadweight
rm EXTRACT.md
git init && git add -A && git commit -m "Add deadweight"
git remote add origin git@github.com:<you>/deadweight.git
git push -u origin main
```

## Then remove it from here

```bash
git rm -r _new-repo-deadweight && git commit -m "Move deadweight to its own repo"
```

Nothing in whyskill imports or depends on this directory. Its own tests live in
`_new-repo-deadweight/tests/` and are not picked up by whyskill's test run, and
whyskill's packaging only includes `whyskill*`, so it is inert where it sits.

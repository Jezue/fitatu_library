# Releasing

A pushed tag `vX.Y.Z` runs `.github/workflows/release.yml`: it runs ruff, mypy and the tests,
builds the sdist and the wheel, fails when the tag is not `v` plus `fitatu_api.__version__`,
runs `twine check` and publishes to PyPI with trusted publishing, so no PyPI token is stored
anywhere. Once the version is on PyPI, a last job publishes `server.json` to the
[MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.Jezue/fitatu-api`. That
job may fail without failing the release: the package is already out, and the job can be
re-run on its own.

## One-time setup (repository owner, done for 0.5.0)

1. PyPI trusted publishing for `fitatu-api` is set up (owner `Jezue`, repository
   `fitatu_library`, workflow `release.yml`, environment `pypi`), under the project's
   *Manage > Publishing* on pypi.org. Redo it only when the repository, the workflow file or
   the environment is renamed; until it matches again, a release fails at *Publish to PyPI*.
2. The GitHub environment `pypi` exists (*Settings > Environments*). Adding yourself as a
   required reviewer makes every publish wait for your approval, and a deployment rule for
   tags `v*` keeps other refs out.
3. Optional, for the weekly *Live contract* workflow that tells when Fitatu changes its API:
   the repository secret `FITATU_SESSION_JSON` with a throwaway account's session. Without it
   the workflow skips with a notice; setting it up is in
   [CONTRIBUTING.md](../CONTRIBUTING.md#detecting-api-changes).
4. The MCP Registry needs no account: the job logs in with the workflow's GitHub OIDC token,
   which lets a repository owned by `Jezue` publish names under `io.github.Jezue/`. The
   registry compares that prefix case-sensitively with the GitHub login, so keep `Jezue` as it
   is in `server.json` and in the README's `mcp-name:` comment. The registry also checks that
   the package's README on PyPI contains that comment, so it has to be in the released README.

## Checklist

1. Refresh the recorded fixtures if the API may have changed since the last recording
   (what they cover: `tests/fixtures/README.md`). The last *Live contract* run, or
   `python scripts/check_contract.py --session session_data.json`, says whether it did: a
   drifted route needs a new recording, and the code that reads it may need a fix.

   ```bash
   fitatu-api login -o session_data.json   # git-ignored; delete it when done
   python scripts/record_fixtures.py --session session_data.json
   git diff tests/fixtures/api_recording.json
   ```

   The script scrubs personal data and then runs a leak check that refuses to write the file
   if a personal value from the raw recording survived. Read the diff anyway: the user id must
   be the synthetic `1234567`, there must be no names, e-mail addresses or meal names of your
   own, and only the changes you expect. Run the tests against the new file before committing it.
   The scenario logs the first catalog hit for "jajko kurze", so when search ranks another product
   first, tests that pin its recorded values (the totals in `tests/test_scripts.py`) need updating.
2. Set `__version__` in `src/fitatu_api/__init__.py`; the package version is read from it.
   Set the same version twice in `server.json` (`version` and the package's `version`);
   `tests/test_docs.py` fails until they match.
3. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and update the link
   references at the bottom of the file (`[X.Y.Z]` to the release tag, `[Unreleased]` to
   `compare/vX.Y.Z...HEAD`).
4. Run the checks CI runs:

   ```bash
   rm -rf dist                # python -m build adds to dist/ and never removes older builds
   hatch run all              # ruff, mypy (src, demo.py, example.py, catering_sync.py), pytest, python -m build
   pre-commit run --all-files
   ```

5. Check that the sdist holds no session files or other local artifacts (the workflow builds
   from the tagged commit, not from your `dist/`):

   ```bash
   python -m tarfile -l dist/fitatu_api-*.tar.gz
   ```

6. Commit the version, `server.json` and the changelog, get them onto `main` and wait for CI
   to pass there; the release workflow runs the tests again, but only on Linux.
7. Tag that commit and push the tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

   Follow the *Release* run under *Actions* (and approve it, if the `pypi` environment asks).
   When it fails before publishing, e.g. on a tag that does not match `__version__`, delete
   the tag (`git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`), fix the cause and tag
   again. PyPI never takes the same version twice, so a published mistake needs a new version.
8. Check the result: `pip install fitatu-api==X.Y.Z` in a fresh environment, and the registry
   entry at
   <https://registry.modelcontextprotocol.io/v0.1/servers/io.github.Jezue%2Ffitatu-api/versions/latest>.
   If the *mcp-registry* job failed, fix the cause and use *Re-run failed jobs*. The job
   publishes the tagged commit's `server.json`; when that file was wrong, publish the fixed one
   by hand with [mcp-publisher](https://github.com/modelcontextprotocol/registry/releases)
   (`mcp-publisher login github`, then `mcp-publisher publish`).
9. Once `pip install fitatu-api==X.Y.Z` works, build the Claude Desktop bundle with
   `python scripts/build_mcpb.py` and create the GitHub release with it (the workflow does not
   create one), with notes taken from the changelog section:

   ```bash
   gh release create vX.Y.Z --verify-tag --title "fitatu-api X.Y.Z" --notes-file notes.md dist/fitatu-X.Y.Z.mcpb
   ```

   The bundle pins `fitatu-api[mcp]==X.Y.Z`, which the host installs from PyPI, so it is no use
   before the release is there. A rebuilt bundle for the same version replaces the asset with
   `gh release upload vX.Y.Z dist/fitatu-X.Y.Z.mcpb --clobber`; Claude Desktop only installs it
   after the old extension is uninstalled, so say so in the release notes.
10. Add an empty `## [Unreleased]` above the new section of `CHANGELOG.md` for the next changes.

## Compatibility policy

Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0 the minor
version stands in for the major one: 0.6 may break what 0.5 offered, a patch release never
does. What counts as the public API is what [API_REFERENCE.md](API_REFERENCE.md) marks stable;
beta and experimental calls can change in a minor release, and the raw responses of Fitatu's
API are outside the promise, since Fitatu changes them. A deprecated call emits a
`DeprecationWarning`, keeps its behaviour unchanged and is removed no earlier than the next minor
release, in the version the changelog names when it is known (`FitatuLibrary`, `user_settings.get()` / `get_for_day()` and `DietPlanModule`: 1.0).

The agent tools are an interface of their own: models, host allowlists and saved MCP permissions
refer to them by name and arguments. A change to them bumps:

| Change | Version part |
|---|---|
| A tool renamed or removed; a parameter renamed, removed, made required or given another type; a read tool turned into a write tool (it leaves `READ_ONLY_TOOLS` and `--read-only`); a change to the result envelope | major |
| A new tool; a new optional parameter; a new field in a result; another annotation change | minor |
| A description, a title or the wording of `SYSTEM_PROMPT` | patch |

Before a tool or parameter is removed, its description and the changelog mark it deprecated for
at least one minor release, and it keeps working until the next major one.

`tests/test_tool_contract.py` compares every tool's name, parameters with their types, required
arguments and annotations with `tests/fixtures/tool_contract.json`, so CI fails on any of these
changes. When the change is deliberate, rewrite the snapshot and commit it with the change:

```bash
python tests/test_tool_contract.py
git diff tests/fixtures/tool_contract.json
```

The changelog entry then says which version part the change needs. In every release, say in the
changelog what changed in the public API and in auth and session handling, and what is not
backwards compatible.

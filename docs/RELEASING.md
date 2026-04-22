# Releasing

This project is structured as a normal Python package with `sdist` and `wheel` builds.

## Release Checklist

1. Update the version in `pyproject.toml` and `src/fitatu_api/__init__.py`.
2. Add a new dated section to `CHANGELOG.md`.
3. Run the full local quality gate:

```bash
ruff check .
mypy src
pytest -q
python -m build
```

4. Review the built artifacts in `dist/`.
5. Tag the release in Git once the public repository is ready.
6. Publish the package artifacts to the chosen package index.
7. Run a final repository hygiene pass:
	- verify `git status` has no local credentials or generated artifacts
	- verify `.gitignore` still covers `session_data.json`, `tmp_*`, `*_report.json`, and `apk_decompilation/`

## Recommended Commands

Build artifacts:

```bash
python -m build
```

Check the archive metadata:

```bash
python -m tarfile -l dist/fitatu_api-*.tar.gz
```

Optional upload flow with `twine`:

```bash
python -m pip install twine
python -m twine check dist/*
python -m twine upload dist/*
```

## Versioning Notes

This package is still in an alpha phase. Prefer small, explicit version bumps with a short changelog entry that describes:

- public API additions
- behavior changes in auth/session handling
- new tested/stable endpoint groups
- backwards-compatibility notes when old imports or wrappers are preserved

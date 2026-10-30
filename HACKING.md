# hacking

## developing

use [uv](https://docs.astral.sh/uv/) to run the app in a development context

```bash
uv run texturefriend
```

common tasks are defined under `[tool.poe.tasks]` in
[pyproject.toml](pyproject.toml) and run with [poe](https://poethepoet.natn.io/)

```bash
uv run poe check
```

## build

a script makes the app for the host platform. mac gets a `.app` windows
`.exe` and linux a binary/AppImage. cross-compiling is not supported.

```bash
./scripts/build.sh
```

| needs            | on    | for                                                     |
| ---------------- | ----- | ------------------------------------------------------- |
| `uv`             | all   | running the build                                       |
| `patchelf`       | linux | nuitka to fix up the rpaths of the bundled qt libraries |
| `libxcb-cursor0` | linux | nuitka to have a copy to bundle into the binary         |

## licences

the about window lists the licences of everything bundled into the app. after
changing a runtime dependency, rebuild the list from the installed packages with

```bash
uv run poe licences
```

## release

pushing a version tag builds for all platforms and publishes the github release.
write what changed under the `## unreleased` heading in `CHANGELOG.md` first,
then run:

```bash
./scripts/release.sh major|minor|patch
```

the script will

1. bump the version
2. retitle `## unreleased` in [CHANGELOG.md](CHANGELOG.md) to the version and
   date being released
3. commit that along with the bump, and tag it
4. print the command that pushes the commit and the tag, so you can check
   before anything leaves your machine

the tag triggers a github workflow, which builds every target, then creates the
release with notes read back out of the changelog section for that version and
the binaries attached.

## sign / notarize (mac only)

signing needs a developer ID Application certificate in a keychain, as documented in the environment variables below

```bash
# build as normal
./scripts/build.sh

# re-sign the bundle with the hardened runtime and notarize it
./scripts/macos-sign.sh

# makes the dmg/zip for release (notarizing the dmg again)
./scripts/package.sh
```

notarization is skipped when the credentials are unset, so it degrades to a
plain code signing step. modern gatekeeper will need both to pass cleanly. 

in CI [macos-keychain.sh](scripts/macos-keychain.sh) runs first to make a 
throwaway keychain out of `MACOS_CERTIFICATE`.

| variable                     | is a                                                            | needed         |
| ---------------------------- | --------------------------------------------------------------- | -------------- |
| `MACOS_SIGN_IDENTITY`        | e.g. `Developer ID Application: Your Name (TEAMID)`             | signing        |
| `APPLE_ID`                   | Apple ID the app-specific password belongs to                   | notarization   |
| `APPLE_APP_PASSWORD`         | [app-specific password](https://support.apple.com/en-us/102654) | notarization   |
| `APPLE_TEAM_ID`              | last part of MACOS_SIGN_IDENTITY                                | notarization   |
| `MACOS_CERTIFICATE`          | developer id application certificate as `.p12`, base64 encoded  | keychain in CI |
| `MACOS_CERTIFICATE_PASSWORD` | password the `.p12` was exported with                           | keychain in CI |

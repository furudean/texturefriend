#!/usr/bin/env sh

set -eu

cd "$(dirname "$0")/.."

# captured first so a failure here is not swallowed by eval
info="$(uv run --no-project python scripts/app-info)"
eval "$info"

target="${1:-}"

if [ -z "$target" ]; then
	case "$(uname -s)" in
		Darwin) os=macos ;;
		Linux) os=linux ;;
		CYGWIN* | MINGW* | MSYS* | Windows_NT) os=windows ;;
		*)
			echo "err: no target name for $(uname -s)" >&2
			exit 1
			;;
	esac

	case "$(uname -m)" in
		arm64 | aarch64) arch=arm64 ;;
		x86_64 | amd64) arch=x86_64 ;;
		*) arch="$(uname -m)" ;;
	esac

	target="$os-$arch"
fi

staging=staging
asset="$NAME-$VERSION-$target"

rm -rf "$staging"
mkdir -p "$staging"

case "$(uname -s)" in
	Darwin)
		ditto -c -k --keepParent "$EXEC_DIRECTORY/$NAME.app" "$staging/$asset.zip"
		./scripts/macos-dmg.sh "$staging/$asset.dmg"
		;;
	CYGWIN* | MINGW* | MSYS* | Windows_NT)
		cp -r "$EXEC_DIRECTORY/$NAME.dist" "$staging/$asset"
		(cd "$staging" && uv run --no-project python -m zipfile -c "$asset.zip" "$asset")
		rm -rf "$staging/$asset"
		;;
	Linux)
		# the AppImage is the one to install, the bare binary is there for
		# systems without FUSE
		cp "$EXEC_DIRECTORY/$NAME.AppImage" "$staging/$asset.AppImage"
		cp "$EXEC_DIRECTORY/$NAME.bin" "$staging/$asset"
		chmod +x "$staging/$asset.AppImage" "$staging/$asset"
		;;
esac

ls -l "$staging"

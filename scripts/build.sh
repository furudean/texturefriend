#!/usr/bin/env sh

set -eu

cd "$(dirname "$0")/.."

info="$(uv run python scripts/app-info)"
eval "$info"

spec=pysidedeploy.spec
generated_spec=pysidedeploy.generated.spec

extra_args="--assume-yes-for-downloads"

extra_args="$extra_args '--company-name=$COMPANY_NAME'"
extra_args="$extra_args '--product-name=$DISPLAY_NAME'"
extra_args="$extra_args '--file-description=$DESCRIPTION'"
extra_args="$extra_args '--file-version=$VERSION'"
extra_args="$extra_args '--product-version=$VERSION'"
extra_args="$extra_args '--copyright=$COPYRIGHT'"

if [ "$(uname -s)" = Darwin ]; then
	extra_args="$extra_args '--macos-app-name=$DISPLAY_NAME'"
	extra_args="$extra_args '--macos-signed-app-name=$BUNDLE_ID'"
	extra_args="$extra_args '--macos-app-version=$VERSION'"
	extra_args="$extra_args '--macos-app-category-type=$MACOS_CATEGORY'"

	# pyside6's wheel is macosx_13_0 which is our baseline
	export MACOSX_DEPLOYMENT_TARGET=13.0
fi

mkdir -p "$EXEC_DIRECTORY"

# nuitka wants .icns on mac, .ico on windows; on linux the onefile binary
# can't embed one anyway, and appimage.sh installs slcachegirl.png itself
mode=onefile
case "$(uname -s)" in
	Darwin) icon=packaging/slcachegirl.icns ;;
	CYGWIN* | MINGW* | MSYS* | Windows_NT)
		icon=packaging/slcachegirl.ico
		mode=standalone
		extra_args="$extra_args '--output-filename=$NAME.exe'"
		;;
	*) icon= ;;
esac

awk -v extra="$extra_args" -v icon="$icon" -v mode="$mode" '
	/^extra_args = / { print $0 " " extra; next }
	/^icon = / { print "icon = " icon; next }
	/^mode = onefile$/ { print "mode = " mode; next }
	{ print }
' "$spec" > "$generated_spec"

uv run python scripts/generate-metadata

rm -rf "$EXEC_DIRECTORY/$NAME.app" "$EXEC_DIRECTORY/$NAME.exe" "$EXEC_DIRECTORY/$NAME.dist" "$EXEC_DIRECTORY/$NAME.bin"

uv run pyside6-deploy --config-file "$generated_spec" --name "$NAME" --force

if [ "$(uname -s)" = Darwin ]; then
	macos="$(sw_vers -productVersion)"

	# actool renders an icon composer bundle only on macos 26
	if [ "${macos%%.*}" -ge 26 ]; then
		./scripts/macos-icon.sh
	else
		echo "note: skipping the icon catalogue, macos 26 is needed"
	fi
fi

if [ "$(uname -s)" = Linux ]; then
	./scripts/appimage.sh
fi

# changelog

all notable changes to this project are documented in this file.

## unreleased

- really fix simple textures being recalculated when showing incomplete textures
- use much less memory indexing textures for search by picture
- decode is ~20% faster
- windows ships as a zip of the app folder instead of a single exe, which defender flagged as a trojan

## v1.1.0 - 2026-10-10

- zoom the preview window with a zoom/pan gestures
- open the preview window at the texture's own size
- fix inspector's checkerboard not tiling from top-left origin
- fix inspector's hairline covering the texture's edge instead of sitting outside it
- fix incomplete textures contributing to simple textures filter
- fix the window stalling while texture characteristics are identified

## v1.0.1 - 2026-10-10

- fix a build error on intel macs

## v1.0.0 - 2026-10-10

- the app is now called TextureFriend
- set TEXTUREFRIEND_LOG env var to see debugging information
- zoom slider in the status bar to grow or shrink textures
- zoom is now more granular, and you can zoom with trackpad gestures
- export as button at the bottom of the inspector
- pick a default export format under export > default format
- edit > copy puts the selected texture on the clipboard. several textures are
  copied as files in the default format
- copy uuid with menu
- clicking on empty space has a right-click menu
- show a progress bar while dragged textures are being written out
- a more visible border hugs selected textures, and textures get rounded corners
- thinner splitter between the grid and inspector
- the inspector syncs its visibility with the toolbar, so it won't get stuck
  hidden.
- more stable scroll position as texture positions shift
- reload drops textures the viewer has evicted from the cache
- drop to match no longer accepts textures that originated in the app (likely
  source of mistakes)
- fix the scroll bar missing its outer edge with the fusion style (linux)
- faster decoding, exporting and filtering overall
- more performant scrolling
- detailed textures no longer shimmer in a small preview window
- bump texture-courier to 0.2.0, pulling in faster and more correct texture
  parsing
- bump other dependencies
- set company name, copyright, version and description on the built executable,
  and an app category on mac. because it makes me feel cool.

## v0.7.3 - 2026-09-14

- fix a build error (again x2)

## v0.7.2 - 2026-09-14

- fix a build error (again)

## v0.7.1 - 2026-09-14

- fix a build error

## v0.7.0 - 2026-09-13

- face lift for color filters
- fix message shown while processing which textures are considered simple
- slightly tweak simple textures algorithm for less false-positives, especially
  with sprite sheets
- can resize grid with default OS zoom-in/zoom-out keybind
- mac build will now use the macos 26+ icon if supported
- show system-appropriate icon in about
- stable scrolling position as viewport changes

## v0.6.0 - 2026-09-03

- one universal mac build instead of separate intel and apple silicon
- tighten simple texture algorithm to include transparent textures, and fix a
  few false positive cases.

## v0.5.4 - 2026-09-03

- builds for intel macs (one more try)

## v0.5.3 - 2026-09-02

- builds for intel macs (hopefully correctly this time)

## v0.5.2 - 2026-09-02

- builds for intel macs
- lowers the mac minimum os requirement to ventura for both architectures

## v0.5.1 - 2026-09-02

- fixes a bug where the texture cache may not open due to integrity checks
- ships a `.dmg` next to the zip
- on mac, share menu bar for all windows

## v0.5.0 - 2026-09-01

- find a texture from a similar image. useful for screenshots
- a failed export no longer leaves a truncated file behind
- set LLTEXTURECACHE_LOG env var to see debugging information
- the app hands its exit code back
- bumps texture-courier dependency, bringing in some minor improvements
- right click menu on the inspector, with the alpha mode shown in it
- drag and drop in the inspector
- better tooltip for the inspector preview
- rename "Transparency Mode" to "Alpha Mode", and "Grid" to "Checkerboard"
- fix sticky swatches
- render alerts as a dialog instead of a line in the status bar

## v0.4.0 - 2026-08-28

- hide simple textures by default (can be shown through view menu)
- make space shortcut a grid-level action instead of global
- smart adjust aspect ratio in preview
- stretch preview instead of letterbox
- separate native View menu items with a separator

## v0.3.1 - 2026-08-27

- no functional changes from v0.3.0 (re-release)

## v0.3.0 - 2026-08-27

- fix regression with encoding
- her background is black-ish purple now. sorry. it wont change any more
- preview is shared between windows

## v0.2.3 - 2026-08-27

- mac app is now notarized

## v0.2.2 - 2026-08-27

- another failed attempt to notarize mac app x-o

## v0.2.1 - 2026-08-27

- failed attempt to notarize mac app o_x

## v0.2.0 - 2026-08-27

- optimize checkerboard sample
- she's pink now (on mac)
- fix load priority in decoding queue
- inspector/preview inherit their transparency
- empty frame on grid
- remove the open button on empty windows
- drag and drop in texturecache dirs
- drag textures out of window to save them as jp2
- transparency settings in View menu

## v0.1.0 - 2026-08-26

- improve algorithm for color filters
- can show incomplete textures
- persistent color swatches, with defaults
- new mac icon

## v0.0.9 - 2026-08-26

- shrink build by excluding unused dependencies
- remove post-tahoe icons
- don't show useless open button
- new icon

## v0.0.8 - 2026-08-25

- appimage build with a temporary icon
- fix setuptools packaging

## v0.0.7 - 2026-08-25

- windows build step that invokes vcvars64
- drop unused nuitka cache

## v0.0.6 - 2026-08-25

- only jump to end of grid on new textures
- pin grid to bottom
- bump color match floor
- homebrew tap, and dumpbin in the windows release
- mark releases as pre-release until assets are uploaded

## v0.0.5 - 2026-08-25

- homebrew tap groundwork
- release script updates

## v0.0.4 - 2026-08-25

- packaging fixes only

## v0.0.3 - 2026-08-25

- use nuitka cache in builds

## v0.0.2 - 2026-08-25

- decode through openJPEG on all platforms
- rework color filters
- use intrinsic size on checkerboard
- "Open Suggested" in menu
- make the picker more clear
- use ccache in build

## v0.0.1 - 2026-08-25

- initial release

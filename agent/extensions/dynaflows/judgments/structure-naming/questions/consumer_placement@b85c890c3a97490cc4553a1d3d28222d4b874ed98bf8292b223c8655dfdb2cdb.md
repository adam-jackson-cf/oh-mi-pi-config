# consumer_placement label guide

The council labelled the 15 real and 40 synthetic `consumer_placement` candidate files with this
guide (structure-naming label guide v7, section K1). Jev is asked the `kind` Choice question in two
option orders; a finding is mean P(`finished_tool`) at or above the threshold.

## K1 consumer_placement (one file inside a package)

- 1: the file is a finished tool people run to view or operate on what the package produces
  (dashboard, report tool, operator console); it reads the package, the package's own code does not
  use it, and it sits inside the package instead of its own root folder.
- 0: the package's own CLI, server or entry point; a view, formatter or renderer the package uses; a
  fixture generator, test executable, migration, build or packaging script; library code.

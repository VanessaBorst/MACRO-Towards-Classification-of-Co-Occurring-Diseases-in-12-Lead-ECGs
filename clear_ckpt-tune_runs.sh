#!/bin/bash

source venv/bin/activate

REL_PATH="savedVM/models/FinalModel_MACRO_MultiBranch_ParamStudy/0905_153928_ml_bs16"

# See https://www.cyberciti.biz/faq/how-to-find-and-delete-directory-recursively-on-linux-or-unix-like-system/
# -exec rm -rf {} + : Execute rm command.
# This variant of the -exec action runs the specified command on the selected files, but the command line is built
# by appending each selected file name at the end; the total number of invocations of the command will be much less than
# the number of matched files.
find $REL_PATH -type d -name 'checkpoint_*' -exec rm -rf {} +

echo "Done!"
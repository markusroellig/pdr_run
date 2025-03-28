#!/bin/bash
# Script to create template directories and copy initial templates

# Create templates directory
mkdir -p /home/roellig/pdr/pdr/pdr_run/pdr_run/models/templates

# Create templates directory in input directory structure if it doesn't exist
# Note: You may need to adjust this path based on your actual PDR_INP_DIRS configuration
if [ -d "$HOME/pdr/pdr/pdr_run/input" ]; then
    mkdir -p "$HOME/pdr/pdr/pdr_run/input/templates"
fi

# Copy the template file
cp /home/roellig/pdr/pdr/pdr_run/pdr_run/models/templates/PDRNEW.INP.template "$HOME/pdr/pdr/pdr_run/input/templates/" 2>/dev/null || true

echo "Template directories created successfully!"
echo "You can now copy your PDRNEW.INP.template file to either:"
echo " - /home/roellig/pdr/pdr/pdr_run/pdr_run/models/templates/"
echo " - The templates directory in your PDR_INP_DIRS path"

chmod +x /home/roellig/pdr/pdr/pdr_run/setup_templates_dir.sh

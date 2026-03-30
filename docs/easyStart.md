# neutronNote: Beta Testers Wanted!

neutronNote is an electronic lab notebook app that runs in a browser and is able to directly access the data inside your proposal. It supports multiple users and has a rich selection of tools to create a real-time narrative of your experiment. The notebook lives in your ipts folder and is protected by the same permissions as your neutron data.

NOTE: neutronNote is still in the development phase, so some bugs may be present and some features may be limited. 

## Installation/Setup

Run this command once in a terminal:

```bash
bash /SNS/SNAP/shared/deploy/neutronote/install.sh
```

This adds the `neutronote` command to your shell. You only need to do this once — it will be available in all future terminal sessions.

## Opening your first neutronNote

```bash
neutronote <IPTS_NUMBER>
```

For example:

```bash
neutronote 33219
```

The server will print a clickable URL — open it in your browser and you're ready to go.

## Overview

The neutronNote UI has two main sections: `Timeline` and `Create Entry`. Various templates in the `Create Entry` section are used to create different blocks of content that, when submitted will appear on the timeline. 

### Text Entries

The simplest entry type is the default `Text` template. When selected, you will see a field to enter an optional title and another field for content. You can enter any text content you like here and, as neutroNote supports markdown you can easily include equations, or code blocks. Another useful feature is the possibilty to add a tag to the entry, say you are running with two samples, "SampleA" and "SampleB", you can create corresponding tags and, afterwords filter the `neutroNote` to only show content with a specific tag.

Once your text is ready, you can press `Submit Entry` and it will appear in the `Timeline` on the right hand side. Note that entries are automatically timestamped and labelled with the user name of whoever posts the entry. Multiple users can access the `neutroNote` in parallel (e.g. one user can post details of an experimental loading while another post details of data analysis).

If you hover over the top right hand corner of the entry, you will see a pencil icon. Clicking this allows you to edit the note. You can also add a tag either by clicking the label icon top right. Existing tags are shown at the bottom of the entry.

### Header Entries

This entry type creates a header in the `neutroNote` with some useful information regarding a neutron run including title, run time, filesize etc. All you need to do is enter a run number. This entry type is useful to embed neutron collection information into the narrative as you build your `neutroNote`.

### Image Entries

This entry type allows you to add digital images to your `neutroNote` by default a file browser will point to your IPTS folder and it is recommended that images be stored here to keep all metadata relevant to your proposal in that folder.

### Code Entries

The code entry type allows you to enter python code and to run it from inside the `neutroNote`. Both mantid and the SNAP-specific python interface `SNAPWrap` are installed and can be invoked. 

Note that, similar to a Jupyter notebook, variables persist across multiple entries in the `neutronNote` so they can be referred to as needed. Like a Jupyter notebook, you have buttons to refresh or restart the underlying kernel. Restarting the kernel will erase all defined data and parameters.

It's important to keep track of your RAM usage and a small informational widget is provided in bottom left of the neutronNote to show how much data are being used. Of particular concern are mantid workspaces which can containing very large neutron datasets. A display of instantiated mantid workspaces is listed in the notebook but, currently, these can't be directly interacted with as you might be used to in mantid workbench.

### Data Entries

The data entry type allows you to embed already reduced data into your `neutroNote`. Clicking on this entry will allow you to select from different instrument configurations from which data have been reduced. Each configuration is labelled with a 16 character hash (e.g.) 0f78eb... or   81e22e, once the correct configuration is selected you can browse the reduced data for that configuration, where you can see available runs, titles, duration and start times. It is possible to filter this list on run duration (e.g. to ignore short alignment or diagnostic runs), run title, or to enter a specific run number

From the run browser, multiple runs can be selected and by checking the boxes to the left of the run numbers. These can then be viewed. 
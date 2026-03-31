# neutroNote: Beta Testers Wanted!

neutroNote is an electronic lab notebook that runs in a browser and directly accesses the data in your proposal. It supports multiple users and provides tools to build a real-time narrative of your experiment. The notebook lives in your IPTS folder and is protected by the same permissions as your neutron data.

> **Note:** neutroNote is still in development — some bugs may be present and some features may be limited.

## Installation / Setup

Run this command once in a terminal:

```bash
bash /SNS/SNAP/shared/deploy/neutronote/install.sh
```

This adds the `neutronote` command to your shell. You only need to do this once — it will be available in all future terminal sessions.

## Opening Your First neutroNote

```bash
neutronote <IPTS_NUMBER>
```

For example:

```bash
neutronote 33219
```

The server will print a clickable URL — open it in your browser and you're ready to go.

## Bugs, comments feature requests

The best way to report bugs or request features is by clicking the issues tab on the github repo: https://github.com/mguthriem/neutronote/issues

You can also email Malcolm Guthrie on guthriem@ornl.gov

## Overview

The neutroNote UI has two main sections: **Timeline** (right) and **Create Entry** (left). You use the templates in **Create Entry** to compose different types of content. When you press **Submit Entry**, the new entry appears on the timeline.

## Tagging

Any entry can be tagged. For example, if you are running with two samples you can create tags like "SampleA" and "SampleB", then filter the timeline to show only entries with a specific tag. Tags are added via the label icon in the top-right corner of each entry and displayed at the bottom of the entry card.

## Entry Types

### Text Entries

The default template. Enter an optional title and any text content. neutroNote supports Markdown, so you can include equations, code blocks, and formatted text.

Entries are automatically timestamped and labelled with the username of the poster. Multiple users can work in parallel — for example, one posts experimental notes while another posts analysis results.

To edit an existing entry, hover over its top-right corner and click the pencil icon.

### Header Entries

Creates a header card with metadata for a neutron run: title, run time, total counts, file size, etc. Just enter a run number. This is useful for embedding run information into your narrative.

### Image Entries

Add digital images to the notebook. A file browser opens to your IPTS folder by default — it is recommended to store images there to keep everything together.

### Code Entries

Enter and execute Python code directly inside neutroNote. Both `mantid` and the SNAP-specific interface `SNAPWrap` are available.

Like a Jupyter notebook, variables persist across code entries and can be referenced later. You can restart the kernel to clear all variables using the kernel controls.

A RAM-usage widget is shown at the bottom-left of the notebook. Keep an eye on this — mantid workspaces can be very large. A list of active workspaces is displayed, though they cannot yet be interacted with directly as in Mantid Workbench.

### Data Entries

Embed already-reduced data into the notebook. Select an instrument configuration (identified by a 16-character hash, e.g. `0f78eb70a1c029b7`), then click **Browse Reduced Runs** to see available runs with titles, durations, and start times. The list can be filtered by duration, title, or run number.

Select multiple runs by checking the boxes beside the run numbers, then view them in a single plot. Standard plot interactions are available (pan, zoom, reset). Use the **Grouping** dropdown to select different pixel groupings; individual spectra within a group can be toggled on and off.

Click **Add to Timeline** to save a snapshot of the plot. From the timeline, click **Expand** to re-open an interactive view of the data.

### PV Log Entries

Plot process variables (PVs) that track quantities such as hydraulic pressure or sample temperature. A curated set of commonly useful PVs is provided.

1. Set the date range (defaults to the experiment dates, but can be edited).
2. Select a PV category (e.g. "pressure").
3. Available PVs with real data are found and plotted as time series.

> **Note:** Downloading PV data can take a moment depending on the date range.

## Timeline

The timeline is a running, time-ordered narrative of the experiment. At the top, the IPTS number and experiment title are displayed.

### Settings (⚙️)

Click the gear icon to edit the experiment title and date range. The date range serves as the default for PV Log entries. Currently neutroNote is not connected to the IPTS database, so title and dates must be entered manually.

### Timeline Controls

- **📄 Export PDF** — exports the entire timeline to a PDF saved at `IPTS-<number>/shared/neutronote_<ipts>.pdf`.
- **Sort** — toggle between oldest-first and newest-first ordering.
- **Reset** — deletes the entire notebook database. **Use with caution.**

### Editing & Interaction

Hover over the top-right corner of any entry to reveal the edit button (pencil icon). Some entry types offer additional interactivity — for example, data entries can be zoomed and panned.

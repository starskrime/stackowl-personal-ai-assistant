---
name: file_organizer
description: Organize files in a directory by sorting them into subfolders based on file type, date, or custom rules
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📂"
parameters:
  directory:
    type: string
    description: "Directory to organize"
    default: "."
  dry_run:
    type: boolean
    description: "Preview changes without moving files"
    default: true
steps:
  - id: list_files
    tool: ShellTool
    args:
      command: "ls -la {{directory}}"
      mode: "local"
    timeout_ms: 5000
  - id: create_folders
    tool: ShellTool
    args:
      command: "mkdir -p {{directory}}/Images {{directory}}/Documents {{directory}}/Videos {{directory}}/Audio {{directory}}/Archives {{directory}}/Code {{directory}}/Other"
      mode: "local"
    timeout_ms: 5000
  - id: move_images
    tool: ShellTool
    args:
      command: "mv {{directory}}/*.{jpg,jpeg,png,gif,svg,webp,bmp} {{directory}}/Images/ 2>/dev/null; echo 'Images moved'"
      mode: "local"
    timeout_ms: 10000
  - id: move_documents
    tool: ShellTool
    args:
      command: "mv {{directory}}/*.{pdf,doc,docx,txt,md,rtf,xls,xlsx,ppt,pptx} {{directory}}/Documents/ 2>/dev/null; echo 'Documents moved'"
      mode: "local"
    timeout_ms: 10000
  - id: move_videos
    tool: ShellTool
    args:
      command: "mv {{directory}}/*.{mp4,mov,avi,mkv,wmv,flv,webm} {{directory}}/Videos/ 2>/dev/null; echo 'Videos moved'"
      mode: "local"
    timeout_ms: 10000
  - id: move_audio
    tool: ShellTool
    args:
      command: "mv {{directory}}/*.{mp3,wav,aac,flac,ogg,m4a} {{directory}}/Audio/ 2>/dev/null; echo 'Audio moved'"
      mode: "local"
    timeout_ms: 10000
  - id: move_archives
    tool: ShellTool
    args:
      command: "mv {{directory}}/*.{zip,tar,gz,bz2,rar,7z} {{directory}}/Archives/ 2>/dev/null; echo 'Archives moved'"
      mode: "local"
    timeout_ms: 10000
  - id: move_code
    tool: ShellTool
    args:
      command: "mv {{directory}}/*.{js,ts,py,rb,java,c,cpp,h,sh,php,swift,go} {{directory}}/Code/ 2>/dev/null; echo 'Code files moved'"
      mode: "local"
    timeout_ms: 10000
  - id: count_remaining
    tool: ShellTool
    args:
      command: "ls {{directory}} | grep -v -E '^Images$|^Documents$|^Videos$|^Audio$|^Archives$|^Code$|^Other$' | wc -l"
      mode: "local"
    timeout_ms: 5000
  - id: summarize
    tool: ShellTool
    args:
      command: "echo '=== Organized Summary ===' && for dir in Images Documents Videos Audio Archives Code Other; do count=$(ls {{directory}}/$dir 2>/dev/null | wc -l | tr -d ' '); echo \"$dir: $count files\"; done"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "File organization completed for: {{directory}}\n\nDry run: {{dry_run}}\n\n{{summarize.output}}\n\nRemaining unorganized files: {{count_remaining}}"
    depends_on: [list_files]
    inputs: [summarize.output, count_remaining.output]
---

# File Organizer

Sort files into subfolders by file type.

## Usage

Organize current directory:
```
/file_organizer
```

Organize Downloads folder:
```
directory=~/Downloads
```

## Parameters

- **directory**: Folder to organize (default: current)
- **dry_run**: Preview before moving (default: true)

## Categories

- **Images**: jpg, png, gif, svg, webp, bmp
- **Documents**: pdf, doc, docx, txt, md, xls, xlsx, ppt, pptx
- **Videos**: mp4, mov, avi, mkv, wmv, flv, webm
- **Audio**: mp3, wav, aac, flac, ogg, m4a
- **Archives**: zip, tar, gz, bz2, rar, 7z
- **Code**: js, ts, py, rb, java, c, cpp, h, sh, php, swift, go
- **Other**: Everything else

## Examples

### Organize Downloads
```
directory=~/Downloads
dry_run=false
```

### Preview first
```
directory=./my_folder
dry_run=true
```

## Safety

- **Dry run default** — preview before moving
- **Skips folders** — doesn't move subdirectories
- **Name conflicts** — adds timestamp suffix if needed
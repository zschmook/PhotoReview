#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

copy_destinations=''
copy_directories=()
dry_run=false
for argument in "$@"; do
    if [[ "$argument" == '--dry-run' ]]; then dry_run=true; fi
done

# Ask before compiling so the finished build can be copied unattended.
if [[ -t 0 && "$dry_run" == false ]]; then
    IFS= read -r -p 'Copy finished EXE to folders (comma-separated; Enter to skip): ' copy_destinations || copy_destinations=''
fi

append_copy_directory() {
    local directory="${copy_field#"${copy_field%%[![:space:]]*}"}"
    directory="${directory%"${directory##*[![:space:]]}"}"
    if [[ -z "$directory" ]]; then return; fi
    if command -v cygpath >/dev/null 2>&1; then directory="$(cygpath -u -- "$directory")"; fi
    copy_directories+=("$directory")
}

# Split only unquoted commas. Backslashes and shell characters remain literal.
copy_field=''
copy_quote=''
after_quote=false
for ((index=0; index<${#copy_destinations}; index++)); do
    character="${copy_destinations:index:1}"
    if [[ -n "$copy_quote" ]]; then
        if [[ "$character" == "$copy_quote" ]]; then
            if [[ "${copy_destinations:index+1:1}" == "$copy_quote" ]]; then
                copy_field+="$character"; index=$((index+1))
            else
                copy_quote=''; after_quote=true
            fi
        else copy_field+="$character"; fi
    elif [[ "$character" == ',' ]]; then
        append_copy_directory; copy_field=''; after_quote=false
    elif [[ "$after_quote" == true && "$character" != [[:space:]] ]]; then
        printf 'Invalid destination list: put a comma after each quoted folder.\n' >&2
        exit 2
    elif [[ "$after_quote" == false && ( "$character" == \" || "$character" == \' ) && -z "${copy_field//[[:space:]]/}" ]]; then
        copy_quote="$character"; copy_field=''
    else copy_field+="$character"; fi
done
if [[ -n "$copy_quote" ]]; then
    printf 'Invalid destination list: a quoted folder is missing its closing quote.\n' >&2
    exit 2
fi
append_copy_directory

.venv/Scripts/python.exe tools/build_windows.py "$@"

if [[ ${#copy_directories[@]} -eq 0 ]]; then exit 0; fi
source_exe='dist/PhotoReview.exe'
# Stage beside the destination; an interrupted copy keeps the old EXE intact.
copy_temporary=''
copy_failed=false
trap 'if [[ -n "$copy_temporary" ]]; then rm -f -- "$copy_temporary" || true; fi' EXIT
for copy_directory in "${copy_directories[@]}"; do
    target_exe="$copy_directory/PhotoReview.exe"
    if ! mkdir -p -- "$copy_directory"; then
        printf 'Build succeeded, but the copy folder could not be created: %s\n' "$copy_directory" >&2
        copy_failed=true; continue
    fi
    if [[ "$source_exe" -ef "$target_exe" ]]; then
        printf 'EXE is already in: %s\n' "$target_exe"
        continue
    fi
    if ! copy_temporary="$(mktemp -- "$copy_directory/.PhotoReview.exe.XXXXXX")" ||
       ! cp -- "$source_exe" "$copy_temporary" ||
       ! mv -f -- "$copy_temporary" "$target_exe"; then
        printf 'Build succeeded at dist/PhotoReview.exe, but the extra copy failed: %s\n' "$target_exe" >&2
        if [[ -n "$copy_temporary" ]]; then rm -f -- "$copy_temporary" || true; fi
        copy_temporary=''; copy_failed=true; continue
    fi
    copy_temporary=''
    printf 'Copied EXE to: %s\n' "$target_exe"
done
if [[ "$copy_failed" == true ]]; then exit 1; fi

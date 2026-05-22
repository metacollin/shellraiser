<p align="center">
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <img src="misc/logo.svg" width="30%">
</p>

<p align="center">
  No tears. Only toolchains.
</p>

# shellraiser:
 *The bash to c compiler no one asked for.* 


Compile Bash scripts to native binaries. The compiled output runs 5-12x faster on computation-heavy workloads, integrates seamlessly with the rest of your shell environment, and can export compiled functions and variables back into plain Bash sessions.

In other words, if you want to use the wrong tool (bash) for the wrong job (a computationally heavy one), shellraiser lets you do the wrong thing *fast*.

Primary legitimate usage case: speed up some of your bash completions. 

## Quick Start
 
```bash
./shellraiser script.sh              #  ->  ./script (native binary)
./shellraiser script.sh -o mybin     # custom output name
./shellraiser script.sh --sourceable # polyglot: source OR execute (see below)
./shellraiser script.sh --emit-only  # just print the generated C
```
 
Requires Python 3.8+, GCC or Clang, Linux or macOS. The output binary is self-contained with no runtime dependencies.
 
## Key Features
 
### Compiled Functions Callable From Any Shell
 
When a compiled script calls `export -f`, the function becomes a real command on `$PATH`. Any shell, script, or tool can call it by name -- it runs as native compiled code, not interpreted Bash.
 
```bash
# utils.sh -- compile with shellraiser
to_upper() { echo "$1" | tr 'a-z' 'A-Z'; }
export -f to_upper
```
 
```bash
# Anywhere else -- plain Bash, xargs, find, parallel:
to_upper "hello"                                    #  ->  HELLO
echo -e "foo\nbar" | xargs -I {} bash -c 'to_upper "{}"'
find . -name "*.txt" -exec to_upper {} \;
```
 
Under the hood, `export -f` creates a shell shim on `$PATH` that routes back into the compiled binary via `--call`. The calling code doesn't know the function is compiled. Cleanup is automatic on exit.
 
This also works in reverse: if a parent Bash session exports a function with `export -f`, the compiled binary can call it. The runtime detects `BASH_FUNC_name%%` environment variables and dispatches through Bash.
 
### Source Compiled Scripts Into Your Shell
 
With `--sourceable`, the output is a single file that can be both executed and sourced:
 
```bash
./shellraiser mylib.sh -o mylib --sourceable
```
 
```bash
./mylib                  # run as a compiled binary
source ./mylib           # load its functions + variables into your shell
```
 
After sourcing, all functions and variables defined in the compiled script are available in your current session. Functions call back into the cached binary, running as native code.
 
```bash
source ./mylib
echo "$VERSION"          # variable from the compiled script
process_data "input"     # compiled function, runs as native code
type process_data        #  ->  "process_data is a function"
```
 
The binary is embedded as a base64 payload, extracted and cached at `~/.cache/shellraiser/` on first run. Subsequent runs skip extraction.
 
### Background Pipelines With Process Monitoring
 
Multi-command pipelines can be backgrounded with proper `$!` tracking. The common pattern of parallel work with a progress-monitoring loop works correctly:
 
```bash
printf "%s\n" "${items[@]}" | xargs -P 4 -I {} bash -c 'do_work "{}"' &
pid=$!
 
while ps -p $pid > /dev/null 2>&1; do
    count=$(find "$tmpdir" -name "*.done" | wc -l)
    printf "\rProgress: %d/%d" "$count" "$total"
    sleep 0.1
done
```
 
The runtime handles backgrounded pipelines by forking a supervisor process, correctly sets `$!`, reaps zombie processes between commands (so `ps -p` detects completion), and guards EXIT traps against firing in child processes.
 
### Associative Arrays
 
Full `declare -A` support backed by a real hash table:
 
```bash
declare -A config
config["host"]="example.com"
config["port"]="8080"
 
for key in "${!config[@]}"; do
    echo "$key = ${config[$key]}"
done
 
echo "entries: ${#config[@]}"
```
 
### Everything Else You'd Expect
 
- **Indexed arrays** with append (`+=`), expansion (`${arr[@]}`), length (`${#arr[@]}`), and command-substitution init (`arr=($(cmd))`)
- **Functions** with `local` scoping, `return`, positional params, `shift`, recursion
- **All control flow**: `if/elif/else`, `while`, `until`, `for-in`, C-style `for((;;))`, `case/esac`, `break`, `continue`, `&&`/`||` chains, subshells
- **Pipes and redirects**: `|`, `>`, `>>`, `<`, `2>`, `2>&1`, `>&2`
- **28 builtins** including `echo -e`, `printf` (with format repetition, `-v`, width specs), `[[ ]]` (with `=~`, `&&`, `||`), `declare`, `trap`, `kill`, `command -v`
- **Special variables**: `$?`, `$$`, `$!`, `$#`, `$@`, `$*`, `$0`-`$9`
- **Extended test**: `[[ ]]` with regex matching (`=~`), pattern globbing (`==`), logical operators
- **ANSI-C quoting**: `$'\n'`, `$'\t'`, `$'\xNN'`
- **String append**: `var+="text"` (in-place, O(1) amortized)
- **Glob expansion**: `*`, `?`, `[...]` in command arguments
- **Trap handling**: `trap 'cmd' EXIT` and signal traps (INT, TERM, HUP, etc.)

## Performance
 
```
bash bench.sh 100000
```
 
Typical results at N=100000 (macOS, x86-64):

```
Test                                       Bash   Compiled   Speedup
----                                       ----   --------   -------
Arithmetic (100000 iterations)           1176ms      130ms     9.0x
String append (100000 iterations)        2138ms     1327ms     1.6x
Function calls (100000 calls)            1638ms      161ms    10.1x
Array append (100000 ops)                1107ms       97ms    11.4x
Assoc array set (20000 ops)               294ms       56ms     5.2x
Assoc array get (20000 ops)               346ms       60ms     5.7x
Conditionals (100000 branches)           1670ms      159ms    10.5x
Nested loops (100x1000)                  1240ms      164ms     7.5x
echo to /dev/null (100000 calls)         7356ms     5415ms     1.3x
printf to /dev/null (100000 calls)       7330ms     5090ms     1.4x
[[ ]] extended test (100000 evals)       1206ms      122ms     9.8x
C-style for loop (100000 iters)           782ms      158ms     4.9x
case/esac (100000 matches)               1259ms      130ms     9.6x
Subshell fork (1000 forks)               1960ms     1652ms     1.1x
```

## Feature Support
 
### ✅ Supported  ⚠️ Partial  ❌ Not Yet
 
#### Variables & Expansion
 
| Builtin ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀| Status | Notes |
|---|---|---|
| `var=value`, `$var`, `${var}` | ✅ |
| Single, double, backslash quoting | ✅ |
| `$'...'` ANSI-C quoting (`\n`, `\t`, `\xNN`, `\0NNN`) | ✅ |
| `$(( arithmetic ))` — all C operators | ✅ |
| `$(command)` substitution | ✅ |
| `` `backtick` `` substitution | ✅ |
| `var+="append"` | ✅ |
| `${#var}` string length | ✅ |
| `${var:-default}` default value | ❌ |
| `${var:=word}`, `${var:+word}`, `${var:?msg}` | ❌ |
| `${var:offset:length}` substring | ❌ |
| `${var//pat/rep}` substitution | ❌ |
| `${var#pat}`, `${var##pat}` prefix strip | ❌ |
| `${var%pat}`, `${var%%pat}` suffix strip | ❌ |
| `${var^^}`, `${var,,}` case conversion | ❌ |
 
#### Control Flow
 
| Builtin ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀| Status | Notes |
|---|---|---|
| `if` / `elif` / `else` / `fi` | ✅ |
| `while`, `until` | ✅ |
| `for var in items` (with word splitting) | ✅ |
| `for (( init; cond; step ))` | ✅ |
| `case` / `esac` (with `*`, `?`, `\|` patterns) | ✅ |
| `break`, `continue` | ✅ |
| `&&`, `\|\|` chains | ✅ |
| Subshells `( ... )` | ✅ |
| Brace groups `{ ...; }` | ✅ |
| `select` | ❌ |
| `coproc` | ❌ |
 
#### Functions
 
| Builtin ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀| Status | Notes |
|---|---|---|
| `name() { body; }` and `function name { body; }` | ✅ |
| `local`, `return`, `shift` | ✅ |
| `$1`–`$9`, `$@`, `$*`, `$#` | ✅ |
| `export -f` (compiled function export via PATH) | ✅ |
| Calling Bash-exported functions (`BASH_FUNC_%%`) | ✅ |
| `--sourceable` polyglot (source or execute) | ✅ |
| Recursive functions | ✅ |
 
#### Arrays
 
| Builtin ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀| Status | Notes |
|---|---|---|
| `arr=(a b c)`, `arr[i]=val` | ✅ |
| `${arr[i]}`, `${arr[@]}`, `${arr[*]}` | ✅ |
| `${#arr[@]}` element count | ✅ |
| `arr+=(val)` append | ✅ |
| `arr=($(cmd))` from command substitution | ✅ |
| `declare -A map` (associative) | ✅ |
| `map["key"]="val"`, `${map[$key]}` | ✅ |
| `${!map[@]}` key iteration | ✅ |
| `${#map[@]}` entry count | ✅ |
| `declare -A map=([k]=v ...)` inline init | ❌ |
 
#### Pipes, Redirection & Jobs
 
| Builtin ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀| Status | Notes |
|---|---|---|
| `cmd1 \| cmd2 \| cmd3` | ✅ |
| `>`, `>>`, `<` | ✅ |
| `2>`, `2>>`, `2>&1`, `>&2` | ✅ |
| `cmd &` (background) | ✅ |
| `cmd1 \| cmd2 &` (background pipeline) | ✅ |                                     
| `$!` (last background PID) | ✅ |
| `$$`, `$?` | ✅ |
| Here-strings `<<<` inside `$()` | ⚠️ |
| Here-documents `<<EOF` | ❌ |
| Process substitution `<(cmd)`, `>(cmd)` | ❌ |
| Arbitrary fd numbers `3>`, `exec 3>` | ❌ |
 
#### Builtins
 
| Builtin ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀| Status | Notes |
|---|---|---|
| `echo` | ✅ | `-n`, `-e`, `-E`, full escape sequences |
| `printf` | ✅ | Format repetition, `-v`, `%b`, `%s`, `%d`, `%f`, `%x`, width/precision |
| `cd` | ✅ | |
| `exit` | ✅ | |
| `read` | ✅ | `-r`, `-p` |
| `export` | ✅ | `-f` for function export |
| `test` / `[` | ✅ | File, string, arithmetic tests |
| `[[` | ✅ | `=~` regex, `==` glob, `&&`, `\|\|`, `!`, `( )` |
| `true` / `false` | ✅ | |
| `return` | ✅ | |
| `local` | ✅ | `-a` for arrays |
| `shift` | ✅ | |
| `wait` | ✅ | Optional PID |
| `unset` | ✅ | |
| `declare` / `typeset` | ✅ | `-a`, `-A`, `-i`, `-x`, `-g`, `-r` |
| `trap` | ✅ | EXIT + signals; PID-guarded against child forks |
| `kill` | ✅ | Signal names and numbers |
| `command` | ✅ | `-v` for path lookup |
| `:` (noop) | ✅ | |
| `source` / `.` | ❌ | Use `--sourceable` instead |
| `eval` | ❌ | |
| `set` / `shopt` | ❌ | |
| `getopts` | ❌ | |
| `mapfile` / `readarray` | ❌ | |
| `pushd` / `popd` | ❌ | |
 
#### Special Variables
 
| Builtin ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀| Status | Notes |
|---|---|---|
| `$0`, `$1`–`$9`, `${10}+` | ✅ |
| `$@`, `$*`, `$#` | ✅ |
| `$?` (exit code), `$$` (PID), `$!` (bg PID) | ✅ |
| `$IFS` | ⚠️ |
| `$RANDOM`, `$LINENO`, `$FUNCNAME`, `$BASH_SOURCE` | ❌ |
| `$PIPESTATUS`, `$BASH_REMATCH` | ❌ |

## Architecture
 
```
script.sh  ->  Tokenizer  ->  Parser  ->  AST  ->  Code Generator  ->  gcc/clang  ->  binary
                                                         ^ 
                                                runtime/bash_runtime.c
```
 
**Tokenizer**: Character-level lexer handling nested quoting, `$()`, `$(())`, `$'...'`, and backticks.
 
**Parser**: Recursive descent producing an AST of pipelines, control flow, functions, and commands.
 
**Code Generator**: Emits C source. Variables live in a hash map with scoped entries. External commands use `fork`/`execvp`. Builtins are direct C function calls.
 
**Runtime**: Hash-mapped variables with scope stack, indexed and associative arrays, 28 builtins, pipeline execution, background jobs with zombie reaping, command substitution via `popen` with full variable sync (scope-aware), word splitting, glob expansion, trap handling, and function export via PATH shims.
 
**Function Export**: `export -f` creates a shim script in a temp `$PATH` directory. The shim calls `binary --call funcname "$@"`. The binary dispatches to the compiled C function. Cleanup is automatic via `atexit`.
 
**Sourceable Mode**: `--sourceable` produces a Bash script with the binary base64-encoded as a heredoc. On first run, it decodes to `~/.cache/shellraiser/<hash>`. When sourced, it runs the cached binary with `--source`, which executes the script body (stdout redirected to stderr) then outputs shell-evaluable commands: variable assignments and function wrappers. The caller `eval`s this output.
 
## Project Structure
 
```
shellraiser/
+-- shellraiser.py               # Bash to C transpiler
+-- runtime/
|   +-- bash_runtime.h           # Runtime API
|   +-- bash_runtime.c           # Runtime implementation
+-- README.md                    # You're reading it right now!
+-- benchmark.sh                 # 14-test comprehensive benchmark
+-- bench.sh                     # Simple perf test
```
 
## License

[MIT](https://mit-license.org/)
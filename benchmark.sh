#!/usr/bin/env bash
# ============================================================================
#  shellraiser comprehensive benchmark
#  Works on Linux and macOS. Requires bash 4+ for assoc array tests.
#
#  Usage:  bash examples/benchmark.sh [N]       -- run under bash
#          ./examples/benchmark [N]              -- run compiled
#          bash examples/run_benchmark.sh [N]    -- side-by-side comparison
# ============================================================================

N=$1
if [ -z "$N" ]; then N=50000; fi

# --- Cross-platform nanosecond timer ---
# Detect best available timer once, then inline it everywhere.
# perl is available on both Linux and macOS out of the box.
if date +%s%N 2>/dev/null | grep -qv N 2>/dev/null; then
    _USE_GDATE=0    # Linux: date supports %N
elif gdate +%s%N > /dev/null 2>&1; then
    _USE_GDATE=1    # macOS with GNU coreutils
else
    _USE_GDATE=2    # fallback: perl (available on macOS and Linux)
fi

_ns() {
    if [ "$_USE_GDATE" = "0" ]; then
        date +%s%N
    elif [ "$_USE_GDATE" = "1" ]; then
        gdate +%s%N
    else
        perl -MTime::HiRes=time -e 'print int(time*1e9)'
    fi
}
export _USE_GDATE
export -f _ns

# Detect bash version for associative array support
_has_assoc=false
if [ "${BASH_VERSINFO[0]:-0}" -ge 4 ] 2>/dev/null; then
    _has_assoc=true
fi

echo ""
echo "============================================================"
echo "  shellraiser benchmark -- $N iterations per test"
echo "============================================================"
echo ""
printf "%-40s %10s  %s\n" "Test" "Time" "Check"
printf "%-40s %10s  %s\n" "----" "----" "-----"

_report() {
    local name="$1"
    local t_start=$2
    local t_end=$3
    local check="$4"
    if [ "$t_start" = "0" ] || [ "$t_end" = "0" ] || [ -z "$t_start" ] || [ -z "$t_end" ]; then
        printf "%-40s %10s  %s\n" "$name" "?" "$check"
    else
        local elapsed=$(( (t_end - t_start) / 1000000 ))
        printf "%-40s %8dms  %s\n" "$name" "$elapsed" "$check"
    fi
}
export -f _report

# === 1. ARITHMETIC ===
t0=$(_ns)
i=0; sum=0
while [ "$i" -lt "$N" ]; do
    sum=$((sum + i * 3 + 17))
    sum=$((sum % 1000000))
    i=$((i + 1))
done
t1=$(_ns)
_report "Arithmetic ($N iterations)" "$t0" "$t1" "sum=$sum"

# === 2. STRING APPEND ===
lim=$((N))
t0=$(_ns)
str=""
i=0
while [ "$i" -lt "$lim" ]; do
    str+="abcdefghij"
    i=$((i + 1))
done
slen=${#str}
t1=$(_ns)
_report "String append ($lim iterations)" "$t0" "$t1" "len=$slen"

# === 3. FUNCTION CALLS ===
increment() {
    local val=$1
    result=$((val + 1))
}
t0=$(_ns)
result=0; i=0
while [ "$i" -lt "$N" ]; do
    increment "$result"
    i=$((i + 1))
done
t1=$(_ns)
_report "Function calls ($N calls)" "$t0" "$t1" "result=$result"

# === 4. ARRAY APPEND ===
lim=$((N))
t0=$(_ns)
arr=()
i=0
while [ "$i" -lt "$lim" ]; do
    arr+=("item_${i}")
    i=$((i + 1))
done
alen=${#arr[@]}
t1=$(_ns)
_report "Array append ($lim ops)" "$t0" "$t1" "len=$alen"

# === 5-6. ASSOCIATIVE ARRAY (bash 4+ / compiled) ===
if [ "$_has_assoc" = true ] || [ -z "$BASH_VERSION" ]; then
    declare -A amap
    lim=$((N / 5))
    t0=$(_ns)
    i=0
    while [ "$i" -lt "$lim" ]; do
        k="key_${i}"
        v="value_${i}"
        amap[$k]="$v"
        i=$((i + 1))
    done
    mlen=${#amap[@]}
    t1=$(_ns)
    _report "Assoc array set ($lim ops)" "$t0" "$t1" "len=$mlen"

    t0=$(_ns)
    i=0; hits=0
    while [ "$i" -lt "$lim" ]; do
        k="key_${i}"
        val=${amap[$k]}
        if [ -n "$val" ]; then
            hits=$((hits + 1))
        fi
        i=$((i + 1))
    done
    t1=$(_ns)
    _report "Assoc array get ($lim ops)" "$t0" "$t1" "hits=$hits"
else
    printf "%-40s %10s  %s\n" "Assoc array set (skipped)" "--" "needs bash 4+"
    printf "%-40s %10s  %s\n" "Assoc array get (skipped)" "--" "needs bash 4+"
fi

# === 7. CONDITIONALS ===
t0=$(_ns)
i=0; a=0; b=0; c=0
while [ "$i" -lt "$N" ]; do
    mod=$((i % 3))
    if [ "$mod" -eq 0 ]; then
        a=$((a + 1))
    elif [ "$mod" -eq 1 ]; then
        b=$((b + 1))
    else
        c=$((c + 1))
    fi
    i=$((i + 1))
done
t1=$(_ns)
_report "Conditionals ($N branches)" "$t0" "$t1" "a=$a b=$b c=$c"

# === 8. NESTED LOOPS ===
inner_n=1000
outer_n=$((N / inner_n))
t0=$(_ns)
outer=0; total=0
while [ "$outer" -lt "$outer_n" ]; do
    inner=0
    while [ "$inner" -lt "$inner_n" ]; do
        total=$((total + outer * inner))
        total=$((total % 999999))
        inner=$((inner + 1))
    done
    outer=$((outer + 1))
done
t1=$(_ns)
_report "Nested loops (${outer_n}x${inner_n})" "$t0" "$t1" "total=$total"

# === 9. ECHO BUILTIN ===
t0=$(_ns)
i=0
while [ "$i" -lt "$N" ]; do
    echo "line $i: testing echo builtin performance" > /dev/null
    i=$((i + 1))
done
t1=$(_ns)
_report "echo to /dev/null ($N calls)" "$t0" "$t1"

# === 10. PRINTF BUILTIN ===
t0=$(_ns)
i=0
while [ "$i" -lt "$N" ]; do
    printf "item %d: %s\n" "$i" "testing" > /dev/null
    i=$((i + 1))
done
t1=$(_ns)
_report "printf to /dev/null ($N calls)" "$t0" "$t1"

# === 11. EXTENDED TEST [[ ]] ===
t0=$(_ns)
i=0; hits=0
while [ "$i" -lt "$N" ]; do
    if [[ "$i" -gt 100 && "$i" -lt 49900 ]]; then
        hits=$((hits + 1))
    fi
    i=$((i + 1))
done
t1=$(_ns)
_report "[[ ]] extended test ($N evals)" "$t0" "$t1" "hits=$hits"

# === 12. C-STYLE FOR LOOP ===
t0=$(_ns)
sum=0
for ((i=0; i<N; i++)); do
    sum=$((sum + i))
    sum=$((sum % 1000000))
done
t1=$(_ns)
_report "C-style for loop ($N iters)" "$t0" "$t1" "sum=$sum"

# === 13. CASE/ESAC ===
t0=$(_ns)
i=0; matches=0
while [ "$i" -lt "$N" ]; do
    word="item_${i}"
    case "$word" in
        item_0)    matches=$((matches + 1)) ;;
        item_1*)   matches=$((matches + 1)) ;;
        *_42)      matches=$((matches + 1)) ;;
        *)         ;;
    esac
    i=$((i + 1))
done
t1=$(_ns)
_report "case/esac ($N matches)" "$t0" "$t1" "matches=$matches"

# === 14. SUBSHELL FORK ===
lim=$((N / 100))
t0=$(_ns)
i=0
while [ "$i" -lt "$lim" ]; do
    (true)
    i=$((i + 1))
done
t1=$(_ns)
_report "Subshell fork ($lim forks)" "$t0" "$t1"

echo ""
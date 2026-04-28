#ifndef BASH_RUNTIME_H
#define BASH_RUNTIME_H
#define _DARWIN_C_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <glob.h>
#include <fnmatch.h>
#include <errno.h>
#include <ctype.h>
#include <stdarg.h>

/* ======================== Dynamic String ======================== */
typedef struct {
    char *data;
    size_t len;
    size_t cap;
} BStr;

BStr   bstr_new(void);
void   bstr_append(BStr *s, const char *text);
void   bstr_append_char(BStr *s, char c);
void   bstr_append_n(BStr *s, const char *text, size_t n);
char  *bstr_release(BStr *s);   /* caller owns returned string */
void   bstr_free(BStr *s);

/* ======================== Variable Table ======================== */
#define VAR_HASH_SIZE 256

typedef struct VarEntry {
    char *name;
    char *value;
    int   exported;
    int   scope;            /* scope depth at which this was set */
    struct VarEntry *next;
} VarEntry;

/* ======================== Argument Stack ======================== */
#define MAX_ARG_DEPTH 64

typedef struct {
    int    argc;
    char **argv;           /* NOT owned – caller keeps them alive */
    int    offset;         /* shift offset */
} ArgFrame;

/* ======================== Array Table ======================== */
typedef struct ArrayEntry {
    char  *name;
    char **elements;       /* dense array of strings (NULL = unset slot) */
    int    len;            /* number of allocated slots */
    int    cap;            /* allocated capacity */
    struct ArrayEntry *next;
} ArrayEntry;

/* ======================== Runtime State ======================== */

/* Function pointer type for compiled bash functions */
typedef int (*BashFuncPtr)(int argc, char **argv);

/* Registered function entry for --call dispatch */
typedef struct {
    const char  *name;
    BashFuncPtr  func;
} FuncEntry;

typedef struct {
    VarEntry   *vars[VAR_HASH_SIZE];
    ArrayEntry *arrays[VAR_HASH_SIZE];
    int       last_exit;
    int       scope_depth;
    ArgFrame  arg_stack[MAX_ARG_DEPTH];
    int       arg_depth;
    int       func_returning;   /* set by 'return' builtin */
    char     *script_arg0;      /* $0 – the script name */
    pid_t     last_bg_pid;      /* $! – last backgrounded PID */
    char     *self_path;        /* absolute path to this binary */
    char     *shim_dir;         /* temp dir for exported function shims */
    FuncEntry *func_table;      /* NULL-terminated array; set by generated main() */
    int        func_count;
} BashRuntime;

extern BashRuntime rt;

/* ======================== Init / Cleanup ======================== */
void rt_init(int argc, char **argv);
void rt_cleanup(void);

/* ======================== Function Export ======================== */
void rt_register_functions(FuncEntry *table, int count);
void rt_export_func(const char *name);          /* create PATH shim for function */
BashFuncPtr rt_find_func(const char *name);     /* lookup compiled function by name */
int  rt_dispatch_call(const char *name, int argc, char **argv);  /* --call handler */

/* ======================== Variables ======================== */
void        rt_set_var(const char *name, const char *value);
const char *rt_get_var(const char *name);       /* returns "" if unset */
void        rt_export_var(const char *name);
void        rt_unset_var(const char *name);

/* ======================== Scope (local) ======================== */
void rt_push_scope(void);
void rt_pop_scope(void);
void rt_set_local(const char *name, const char *value);

/* ======================== Positional Params ======================== */
void        rt_push_args(int argc, char **argv);
void        rt_pop_args(void);
int         rt_get_argc(void);          /* $# */
const char *rt_get_arg(int n);          /* $1..$N */
const char *rt_get_arg0(void);          /* $0 */
void        rt_shift_args(int n);       /* shift N */

/* ======================== String Helpers ======================== */
char *rt_strdup_safe(const char *s);            /* strdup that handles NULL */
char *rt_concat(const char *a, const char *b);  /* caller frees */
char *rt_concat3(const char *a, const char *b, const char *c);
char *rt_itoa(long val);                        /* caller frees */
char *rt_join_args(const char *sep);            /* caller frees */

/* ======================== Command Execution ======================== */
int rt_exec_simple(char **argv);
int rt_exec_redir(char **argv,
                  const char *in_file,
                  const char *out_file,  int out_append,
                  const char *err_file,  int err_append);
int rt_exec_pipeline_v(char ***cmds, int ncmds);

/* ======================== Background Execution ======================== */
int  rt_exec_background(char **argv);          /* fork + don't wait; sets $! */
int  rt_exec_background_redir(char **argv,
                               const char *in_file,
                               const char *out_file, int out_append,
                               const char *err_file, int err_append);

/* ======================== Arrays ======================== */
void        rt_array_set(const char *name, int index, const char *value);
const char *rt_array_get(const char *name, int index);  /* returns "" if unset */
int         rt_array_len(const char *name);              /* number of set elements */
int         rt_array_max_index(const char *name);        /* highest allocated index + 1 */
void        rt_array_set_list(const char *name, int count, char **values);
void        rt_array_append(const char *name, const char *value);  /* arr+=(val) */
void        rt_array_unset(const char *name);
char       *rt_array_join(const char *name, const char *sep);  /* "${arr[*]}" – caller frees */
char      **rt_array_get_all(const char *name, int *out_count); /* "${arr[@]}" – caller frees array, NOT strings */

/* ======================== Word Splitting ======================== */
char **rt_split_words(const char *str, int *out_count);  /* split by IFS; caller frees array AND strings */
void   rt_split_free(char **words, int count);

/* ======================== Command Substitution ======================== */
char *rt_cmd_subst(const char *cmd);    /* caller frees */
char *rt_cmd_subst_stdin(const char *cmd, const char *input);  /* caller frees */

/* ======================== Arithmetic ======================== */
long  rt_arith_eval(const char *expr);
char *rt_arith_str(const char *expr);   /* caller frees */

/* ======================== Glob Expansion ======================== */
char **rt_glob_expand(const char *pattern, int *out_count);
void   rt_glob_free(char **results, int count);

/* ======================== Builtins ======================== */
typedef int (*BuiltinFunc)(int argc, char **argv);
BuiltinFunc rt_find_builtin(const char *name);

int rt_builtin_echo(int argc, char **argv);
int rt_builtin_printf_cmd(int argc, char **argv);
int rt_builtin_cd(int argc, char **argv);
int rt_builtin_exit(int argc, char **argv);
int rt_builtin_read(int argc, char **argv);
int rt_builtin_export(int argc, char **argv);
int rt_builtin_test(int argc, char **argv);
int rt_builtin_true_cmd(int argc, char **argv);
int rt_builtin_false_cmd(int argc, char **argv);
int rt_builtin_return_cmd(int argc, char **argv);
int rt_builtin_local_cmd(int argc, char **argv);
int rt_builtin_shift_cmd(int argc, char **argv);
int rt_builtin_wait_cmd(int argc, char **argv);
int rt_builtin_unset_cmd(int argc, char **argv);

/* ======================== Utility ======================== */
int  rt_is_true(void);                  /* last_exit == 0 */
void rt_import_env(void);               /* import environ into var table */
void rt_sync_env(void);                 /* push exported vars to environ */

#endif /* BASH_RUNTIME_H */
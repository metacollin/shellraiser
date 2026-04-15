#define _POSIX_C_SOURCE 200809L
#define _GNU_SOURCE
#include "bash_runtime.h"
#include <sys/stat.h>
#include <signal.h>

extern char **environ;

BashRuntime rt;

/* ================================================================
 *  Dynamic String (BStr)
 * ================================================================ */

BStr bstr_new(void) {
    BStr s;
    s.cap  = 64;
    s.len  = 0;
    s.data = (char *)malloc(s.cap);
    s.data[0] = '\0';
    return s;
}

static void bstr_grow(BStr *s, size_t need) {
    if (s->len + need + 1 > s->cap) {
        while (s->len + need + 1 > s->cap) s->cap *= 2;
        s->data = (char *)realloc(s->data, s->cap);
    }
}

void bstr_append(BStr *s, const char *text) {
    if (!text) return;
    size_t n = strlen(text);
    bstr_grow(s, n);
    memcpy(s->data + s->len, text, n);
    s->len += n;
    s->data[s->len] = '\0';
}

void bstr_append_char(BStr *s, char c) {
    bstr_grow(s, 1);
    s->data[s->len++] = c;
    s->data[s->len] = '\0';
}

void bstr_append_n(BStr *s, const char *text, size_t n) {
    if (!text || n == 0) return;
    bstr_grow(s, n);
    memcpy(s->data + s->len, text, n);
    s->len += n;
    s->data[s->len] = '\0';
}

char *bstr_release(BStr *s) {
    char *r = s->data;
    s->data = NULL;
    s->len = s->cap = 0;
    return r;
}

void bstr_free(BStr *s) {
    free(s->data);
    s->data = NULL;
    s->len = s->cap = 0;
}

/* ================================================================
 *  Hash helpers
 * ================================================================ */

static unsigned int hash_name(const char *s) {
    unsigned int h = 5381;
    while (*s) h = h * 33 + (unsigned char)*s++;
    return h % VAR_HASH_SIZE;
}

/* ================================================================
 *  Init / Cleanup
 * ================================================================ */

void rt_init(int argc, char **argv) {
    memset(&rt, 0, sizeof(rt));
    rt.script_arg0 = argv[0] ? strdup(argv[0]) : strdup("bash2c");

    /* push initial argument frame (the script args) */
    rt.arg_stack[0].argc = argc > 0 ? argc - 1 : 0;
    rt.arg_stack[0].argv = argv;
    rt.arg_stack[0].offset = 0;
    rt.arg_depth = 0;

    /* Set up SIGCHLD handler — use SA_RESTART so syscalls aren't
     * interrupted by child exits.  We do NOT use SA_NOCLDWAIT because
     * that would auto-reap children before wait() can collect them. */
    struct sigaction sa;
    sa.sa_handler = SIG_DFL;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESTART;
    sigaction(SIGCHLD, &sa, NULL);

    /* import existing environment variables */
    rt_import_env();
}

void rt_cleanup(void) {
    for (int i = 0; i < VAR_HASH_SIZE; i++) {
        VarEntry *e = rt.vars[i];
        while (e) {
            VarEntry *next = e->next;
            free(e->name);
            free(e->value);
            free(e);
            e = next;
        }
        rt.vars[i] = NULL;
    }
    for (int i = 0; i < VAR_HASH_SIZE; i++) {
        ArrayEntry *a = rt.arrays[i];
        while (a) {
            ArrayEntry *next = a->next;
            for (int j = 0; j < a->len; j++) free(a->elements[j]);
            free(a->elements);
            free(a->name);
            free(a);
            a = next;
        }
        rt.arrays[i] = NULL;
    }
    free(rt.script_arg0);
}

void rt_import_env(void) {
    if (!environ) return;
    for (char **ep = environ; *ep; ep++) {
        char *eq = strchr(*ep, '=');
        if (!eq) continue;
        size_t nlen = (size_t)(eq - *ep);
        char *name = (char *)malloc(nlen + 1);
        memcpy(name, *ep, nlen);
        name[nlen] = '\0';
        rt_set_var(name, eq + 1);
        /* mark as exported */
        unsigned int h = hash_name(name);
        for (VarEntry *e = rt.vars[h]; e; e = e->next) {
            if (strcmp(e->name, name) == 0) { e->exported = 1; break; }
        }
        free(name);
    }
}

void rt_sync_env(void) {
    for (int i = 0; i < VAR_HASH_SIZE; i++) {
        for (VarEntry *e = rt.vars[i]; e; e = e->next) {
            if (e->exported) setenv(e->name, e->value, 1);
        }
    }
}

/* Push ALL variables to the environment (for command substitution,
 * where the child shell should see all script variables). */
static void rt_sync_all_env(void) {
    for (int i = 0; i < VAR_HASH_SIZE; i++) {
        for (VarEntry *e = rt.vars[i]; e; e = e->next) {
            setenv(e->name, e->value, 1);
        }
    }
}

/* ================================================================
 *  Variables
 * ================================================================ */

static VarEntry *find_var(const char *name) {
    unsigned int h = hash_name(name);
    for (VarEntry *e = rt.vars[h]; e; e = e->next)
        if (strcmp(e->name, name) == 0) return e;
    return NULL;
}

void rt_set_var(const char *name, const char *value) {
    VarEntry *e = find_var(name);
    if (e) {
        free(e->value);
        e->value = strdup(value ? value : "");
        if (e->exported) setenv(name, e->value, 1);
        return;
    }
    unsigned int h = hash_name(name);
    e = (VarEntry *)calloc(1, sizeof(VarEntry));
    e->name  = strdup(name);
    e->value = strdup(value ? value : "");
    e->scope = 0;  /* plain assignments always create at global scope */
    e->next  = rt.vars[h];
    rt.vars[h] = e;
}

const char *rt_get_var(const char *name) {
    VarEntry *e = find_var(name);
    if (e) return e->value;
    /* fall back to environment */
    const char *ev = getenv(name);
    return ev ? ev : "";
}

void rt_export_var(const char *name) {
    VarEntry *e = find_var(name);
    if (!e) {
        /* create empty exported variable */
        rt_set_var(name, "");
        e = find_var(name);
    }
    if (e) {
        e->exported = 1;
        setenv(name, e->value, 1);
    }
}

void rt_unset_var(const char *name) {
    unsigned int h = hash_name(name);
    VarEntry **pp = &rt.vars[h];
    while (*pp) {
        if (strcmp((*pp)->name, name) == 0) {
            VarEntry *e = *pp;
            *pp = e->next;
            if (e->exported) unsetenv(name);
            free(e->name);
            free(e->value);
            free(e);
            return;
        }
        pp = &(*pp)->next;
    }
}

/* ================================================================
 *  Scope (local variables)
 * ================================================================ */

void rt_push_scope(void) {
    rt.scope_depth++;
}

void rt_pop_scope(void) {
    /* remove all vars at current scope depth */
    for (int i = 0; i < VAR_HASH_SIZE; i++) {
        VarEntry **pp = &rt.vars[i];
        while (*pp) {
            if ((*pp)->scope == rt.scope_depth) {
                VarEntry *e = *pp;
                *pp = e->next;
                free(e->name);
                free(e->value);
                free(e);
            } else {
                pp = &(*pp)->next;
            }
        }
    }
    rt.scope_depth--;
}

void rt_set_local(const char *name, const char *value) {
    /* if a variable with this name already exists at current scope, update it */
    VarEntry *e = find_var(name);
    if (e && e->scope == rt.scope_depth) {
        free(e->value);
        e->value = strdup(value ? value : "");
        return;
    }
    /* create new entry at current scope (shadows any outer one) */
    unsigned int h = hash_name(name);
    e = (VarEntry *)calloc(1, sizeof(VarEntry));
    e->name  = strdup(name);
    e->value = strdup(value ? value : "");
    e->scope = rt.scope_depth;
    e->next  = rt.vars[h];
    rt.vars[h] = e;
}

/* ================================================================
 *  Positional Parameters
 * ================================================================ */

void rt_push_args(int argc, char **argv) {
    if (rt.arg_depth + 1 >= MAX_ARG_DEPTH) {
        fprintf(stderr, "bash2c: argument stack overflow\n");
        exit(2);
    }
    rt.arg_depth++;
    /* argc-1 to exclude argv[0] (the function name), matching rt_init */
    rt.arg_stack[rt.arg_depth].argc   = argc > 0 ? argc - 1 : 0;
    rt.arg_stack[rt.arg_depth].argv   = argv;
    rt.arg_stack[rt.arg_depth].offset = 0;
}

void rt_pop_args(void) {
    if (rt.arg_depth > 0) rt.arg_depth--;
}

int rt_get_argc(void) {
    ArgFrame *f = &rt.arg_stack[rt.arg_depth];
    int c = f->argc - f->offset;
    return c > 0 ? c : 0;
}

const char *rt_get_arg(int n) {
    ArgFrame *f = &rt.arg_stack[rt.arg_depth];
    int idx = n + f->offset;
    if (idx < 1 || idx > f->argc) return "";
    if (!f->argv[idx]) return "";
    return f->argv[idx];
}

const char *rt_get_arg0(void) {
    return rt.script_arg0 ? rt.script_arg0 : "";
}

void rt_shift_args(int n) {
    ArgFrame *f = &rt.arg_stack[rt.arg_depth];
    f->offset += n;
    if (f->offset > f->argc) f->offset = f->argc;
}

/* ================================================================
 *  String Helpers
 * ================================================================ */

char *rt_strdup_safe(const char *s) {
    return strdup(s ? s : "");
}

char *rt_concat(const char *a, const char *b) {
    if (!a) a = "";
    if (!b) b = "";
    size_t la = strlen(a), lb = strlen(b);
    char *r = (char *)malloc(la + lb + 1);
    memcpy(r, a, la);
    memcpy(r + la, b, lb + 1);
    return r;
}

char *rt_concat3(const char *a, const char *b, const char *c) {
    char *ab = rt_concat(a, b);
    char *r  = rt_concat(ab, c);
    free(ab);
    return r;
}

char *rt_itoa(long val) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%ld", val);
    return strdup(buf);
}

char *rt_join_args(const char *sep) {
    BStr s = bstr_new();
    int argc = rt_get_argc();
    for (int i = 1; i <= argc; i++) {
        if (i > 1) bstr_append(&s, sep);
        bstr_append(&s, rt_get_arg(i));
    }
    return bstr_release(&s);
}

/* ================================================================
 *  Command Execution
 * ================================================================ */

static char *find_in_path(const char *cmd) {
    if (strchr(cmd, '/')) return strdup(cmd);
    const char *path = getenv("PATH");
    if (!path) path = "/usr/bin:/bin";
    char *p = strdup(path);
    char *dir = strtok(p, ":");
    while (dir) {
        char buf[4096];
        snprintf(buf, sizeof(buf), "%s/%s", dir, cmd);
        if (access(buf, X_OK) == 0) { free(p); return strdup(buf); }
        dir = strtok(NULL, ":");
    }
    free(p);
    return NULL;
}

int rt_exec_simple(char **argv) {
    if (!argv || !argv[0]) { rt.last_exit = 0; return 0; }

    /* check builtins first */
    BuiltinFunc bf = rt_find_builtin(argv[0]);
    if (bf) {
        int argc = 0;
        while (argv[argc]) argc++;
        rt.last_exit = bf(argc, argv);
        return rt.last_exit;
    }

    /* sync exported vars before fork */
    rt_sync_env();

    pid_t pid = fork();
    if (pid < 0) { perror("fork"); rt.last_exit = 127; return 127; }
    if (pid == 0) {
        /* child */
        execvp(argv[0], argv);
        fprintf(stderr, "%s: command not found\n", argv[0]);
        _exit(127);
    }
    int status;
    waitpid(pid, &status, 0);
    rt.last_exit = WIFEXITED(status) ? WEXITSTATUS(status) : 128;
    return rt.last_exit;
}

/* Helper: check if a redirect target is "&N" (fd duplication) or "&-" (close).
 * Returns the target fd number, or -1 for close, or -2 if not an fd dup. */
static int parse_fd_dup(const char *target) {
    if (!target || target[0] != '&') return -2;
    if (target[1] == '-' && target[2] == '\0') return -1; /* close */
    return (int)strtol(target + 1, NULL, 10);
}

/* Apply a redirect: either open a file or dup an fd.
 * src_fd is the fd being redirected (e.g. STDOUT_FILENO or STDERR_FILENO).
 * Returns the saved original fd (for restore), or -1 if nothing was done. */
static int apply_redir(int src_fd, const char *target, int append) {
    if (!target) return -1;
    int dup_target = parse_fd_dup(target);
    if (dup_target == -1) {
        /* &- : close */
        int saved = dup(src_fd);
        close(src_fd);
        return saved;
    }
    if (dup_target >= 0) {
        /* &N : dup fd N to src_fd */
        int saved = dup(src_fd);
        dup2(dup_target, src_fd);
        return saved;
    }
    /* regular file */
    int flags = (src_fd == STDIN_FILENO)
        ? O_RDONLY
        : (O_WRONLY | O_CREAT | (append ? O_APPEND : O_TRUNC));
    int fd = open(target, flags, 0644);
    if (fd < 0) { perror(target); return -1; }
    int saved = dup(src_fd);
    dup2(fd, src_fd);
    close(fd);
    return saved;
}

/* Same as apply_redir but for child process (_exit on error, no save needed). */
static void apply_redir_child(int src_fd, const char *target, int append) {
    if (!target) return;
    int dup_target = parse_fd_dup(target);
    if (dup_target == -1) { close(src_fd); return; }
    if (dup_target >= 0)  { dup2(dup_target, src_fd); return; }
    int flags = (src_fd == STDIN_FILENO)
        ? O_RDONLY
        : (O_WRONLY | O_CREAT | (append ? O_APPEND : O_TRUNC));
    int fd = open(target, flags, 0644);
    if (fd < 0) { perror(target); _exit(1); }
    dup2(fd, src_fd); close(fd);
}

int rt_exec_redir(char **argv,
                  const char *in_file,
                  const char *out_file, int out_append,
                  const char *err_file, int err_append)
{
    if (!argv || !argv[0]) { rt.last_exit = 0; return 0; }

    /* builtins with redirection – handle via temporary fd swapping */
    BuiltinFunc bf = rt_find_builtin(argv[0]);
    if (bf) {
        int saved_in  = apply_redir(STDIN_FILENO,  in_file,  0);
        int saved_out = apply_redir(STDOUT_FILENO, out_file, out_append);
        int saved_err = apply_redir(STDERR_FILENO, err_file, err_append);

        int argc = 0;
        while (argv[argc]) argc++;
        rt.last_exit = bf(argc, argv);

        /* restore */
        if (saved_in  >= 0) { dup2(saved_in,  STDIN_FILENO);  close(saved_in);  }
        if (saved_out >= 0) { dup2(saved_out, STDOUT_FILENO); close(saved_out); }
        if (saved_err >= 0) { dup2(saved_err, STDERR_FILENO); close(saved_err); }
        return rt.last_exit;
    }

    rt_sync_env();

    pid_t pid = fork();
    if (pid < 0) { perror("fork"); rt.last_exit = 127; return 127; }
    if (pid == 0) {
        apply_redir_child(STDIN_FILENO,  in_file,  0);
        apply_redir_child(STDOUT_FILENO, out_file, out_append);
        apply_redir_child(STDERR_FILENO, err_file, err_append);
        execvp(argv[0], argv);
        fprintf(stderr, "%s: command not found\n", argv[0]);
        _exit(127);
    }
    int status;
    waitpid(pid, &status, 0);
    rt.last_exit = WIFEXITED(status) ? WEXITSTATUS(status) : 128;
    return rt.last_exit;
}

int rt_exec_pipeline_v(char ***cmds, int ncmds) {
    if (ncmds == 0) return 0;
    if (ncmds == 1) return rt_exec_simple(cmds[0]);

    rt_sync_env();

    int prev_fd = -1;
    pid_t *pids = (pid_t *)calloc((size_t)ncmds, sizeof(pid_t));

    for (int i = 0; i < ncmds; i++) {
        int pipefd[2] = {-1, -1};
        if (i < ncmds - 1) {
            if (pipe(pipefd) < 0) { perror("pipe"); free(pids); return 1; }
        }

        pid_t pid = fork();
        if (pid < 0) { perror("fork"); free(pids); return 1; }

        if (pid == 0) {
            /* child */
            if (prev_fd >= 0) { dup2(prev_fd, STDIN_FILENO); close(prev_fd); }
            if (pipefd[1] >= 0) { dup2(pipefd[1], STDOUT_FILENO); close(pipefd[1]); }
            if (pipefd[0] >= 0) close(pipefd[0]);

            /* check builtins in child too */
            BuiltinFunc bf = rt_find_builtin(cmds[i][0]);
            if (bf) {
                int argc = 0;
                while (cmds[i][argc]) argc++;
                _exit(bf(argc, cmds[i]));
            }
            execvp(cmds[i][0], cmds[i]);
            fprintf(stderr, "%s: command not found\n", cmds[i][0]);
            _exit(127);
        }

        pids[i] = pid;
        if (prev_fd >= 0) close(prev_fd);
        if (pipefd[1] >= 0) close(pipefd[1]);
        prev_fd = pipefd[0];
    }

    /* wait for all children */
    int status;
    for (int i = 0; i < ncmds; i++) {
        waitpid(pids[i], &status, 0);
    }
    /* last command's exit code */
    rt.last_exit = WIFEXITED(status) ? WEXITSTATUS(status) : 128;
    free(pids);
    return rt.last_exit;
}

/* ================================================================
 *  Command Substitution
 * ================================================================ */

char *rt_cmd_subst(const char *cmd) {
    if (!cmd || !*cmd) return strdup("");

    rt_sync_all_env();  /* child shell must see all script variables */

    FILE *fp = popen(cmd, "r");
    if (!fp) return strdup("");

    BStr result = bstr_new();
    char buf[4096];
    while (fgets(buf, sizeof(buf), fp))
        bstr_append(&result, buf);
    int st = pclose(fp);
    rt.last_exit = WIFEXITED(st) ? WEXITSTATUS(st) : 128;

    char *s = bstr_release(&result);
    /* strip trailing newlines (bash behavior) */
    size_t len = strlen(s);
    while (len > 0 && s[len - 1] == '\n') s[--len] = '\0';
    return s;
}

char *rt_cmd_subst_stdin(const char *cmd, const char *input) {
    if (!cmd || !*cmd) return strdup("");

    rt_sync_all_env();

    /* Create pipe for child's stdin */
    int in_pipe[2];
    int out_pipe[2];
    if (pipe(in_pipe) < 0 || pipe(out_pipe) < 0) return strdup("");

    pid_t pid = fork();
    if (pid < 0) { return strdup(""); }

    if (pid == 0) {
        /* child */
        close(in_pipe[1]);  /* close write end of input pipe */
        close(out_pipe[0]); /* close read end of output pipe */
        dup2(in_pipe[0], STDIN_FILENO);
        dup2(out_pipe[1], STDOUT_FILENO);
        close(in_pipe[0]);
        close(out_pipe[1]);
        execl("/bin/sh", "sh", "-c", cmd, (char *)NULL);
        _exit(127);
    }

    /* parent */
    close(in_pipe[0]);   /* close read end of input pipe */
    close(out_pipe[1]);  /* close write end of output pipe */

    /* Write input to child's stdin */
    if (input && *input) {
        size_t ilen = strlen(input);
        /* Use write() in a loop; ignore partial writes for simplicity */
        size_t written = 0;
        while (written < ilen) {
            ssize_t n = write(in_pipe[1], input + written, ilen - written);
            if (n <= 0) break;
            written += (size_t)n;
        }
    }
    close(in_pipe[1]);  /* signal EOF to child */

    /* Read child's stdout */
    BStr result = bstr_new();
    char buf[4096];
    ssize_t n;
    while ((n = read(out_pipe[0], buf, sizeof(buf) - 1)) > 0) {
        buf[n] = '\0';
        bstr_append(&result, buf);
    }
    close(out_pipe[0]);

    int status;
    waitpid(pid, &status, 0);
    rt.last_exit = WIFEXITED(status) ? WEXITSTATUS(status) : 128;

    char *s = bstr_release(&result);
    size_t len = strlen(s);
    while (len > 0 && s[len - 1] == '\n') s[--len] = '\0';
    return s;
}

/* ================================================================
 *  Arithmetic Evaluator
 *  Supports: + - * / % ** == != < > <= >= && || ! ~ & | ^ << >> ?: ()
 *  Variables are looked up without $ prefix (bash behavior in $(()))
 * ================================================================ */

typedef struct {
    const char *src;
    int pos;
} ArithCtx;

static void arith_skip_ws(ArithCtx *ctx) {
    while (ctx->src[ctx->pos] && isspace((unsigned char)ctx->src[ctx->pos]))
        ctx->pos++;
}

static long arith_expr(ArithCtx *ctx);

static long arith_primary(ArithCtx *ctx) {
    arith_skip_ws(ctx);
    char c = ctx->src[ctx->pos];

    if (c == '(') {
        ctx->pos++;
        long v = arith_expr(ctx);
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == ')') ctx->pos++;
        return v;
    }
    if (c == '!' && ctx->src[ctx->pos + 1] != '=') {
        ctx->pos++;
        return !arith_primary(ctx);
    }
    if (c == '~') {
        ctx->pos++;
        return ~arith_primary(ctx);
    }
    if (c == '-' && !(isdigit((unsigned char)ctx->src[ctx->pos + 1]) == 0)) {
        /* could be unary minus */
        /* always treat as unary minus */
    }
    if (c == '+' || c == '-') {
        ctx->pos++;
        long v = arith_primary(ctx);
        return c == '-' ? -v : v;
    }
    if (isdigit((unsigned char)c)) {
        long v = 0;
        while (isdigit((unsigned char)ctx->src[ctx->pos])) {
            v = v * 10 + (ctx->src[ctx->pos] - '0');
            ctx->pos++;
        }
        return v;
    }
    /* variable name */
    if (isalpha((unsigned char)c) || c == '_') {
        BStr name = bstr_new();
        while (isalnum((unsigned char)ctx->src[ctx->pos]) || ctx->src[ctx->pos] == '_') {
            bstr_append_char(&name, ctx->src[ctx->pos]);
            ctx->pos++;
        }
        char *n = bstr_release(&name);
        /* check for assignment: var = expr, var += expr, etc. */
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == '=' && ctx->src[ctx->pos + 1] != '=') {
            ctx->pos++;
            long v = arith_expr(ctx);
            char *vs = rt_itoa(v);
            rt_set_var(n, vs);
            free(vs);
            free(n);
            return v;
        }
        if ((ctx->src[ctx->pos] == '+' || ctx->src[ctx->pos] == '-' ||
             ctx->src[ctx->pos] == '*' || ctx->src[ctx->pos] == '/') &&
            ctx->src[ctx->pos + 1] == '=') {
            char op = ctx->src[ctx->pos];
            ctx->pos += 2;
            long rhs = arith_expr(ctx);
            long cur = strtol(rt_get_var(n), NULL, 10);
            long v;
            switch (op) {
                case '+': v = cur + rhs; break;
                case '-': v = cur - rhs; break;
                case '*': v = cur * rhs; break;
                case '/': v = rhs ? cur / rhs : 0; break;
                default: v = 0;
            }
            char *vs = rt_itoa(v);
            rt_set_var(n, vs);
            free(vs);
            free(n);
            return v;
        }
        /* post-increment / post-decrement */
        if (ctx->src[ctx->pos] == '+' && ctx->src[ctx->pos + 1] == '+') {
            ctx->pos += 2;
            long v = strtol(rt_get_var(n), NULL, 10);
            char *vs = rt_itoa(v + 1);
            rt_set_var(n, vs);
            free(vs);
            free(n);
            return v;
        }
        if (ctx->src[ctx->pos] == '-' && ctx->src[ctx->pos + 1] == '-') {
            ctx->pos += 2;
            long v = strtol(rt_get_var(n), NULL, 10);
            char *vs = rt_itoa(v - 1);
            rt_set_var(n, vs);
            free(vs);
            free(n);
            return v;
        }
        const char *val = rt_get_var(n);
        long v = strtol(val, NULL, 10);
        free(n);
        return v;
    }
    /* unknown – return 0 */
    return 0;
}

/* operator precedence climbing */
static long arith_mul(ArithCtx *ctx) {
    long v = arith_primary(ctx);
    for (;;) {
        arith_skip_ws(ctx);
        char c = ctx->src[ctx->pos];
        if (c == '*' && ctx->src[ctx->pos+1] == '*') {
            /* exponentiation */
            ctx->pos += 2;
            long e = arith_primary(ctx);
            long r = 1;
            for (long i = 0; i < e; i++) r *= v;
            v = r;
        } else if (c == '*') {
            ctx->pos++; v *= arith_primary(ctx);
        } else if (c == '/' && ctx->src[ctx->pos+1] != '=') {
            ctx->pos++;
            long d = arith_primary(ctx);
            v = d ? v / d : 0;
        } else if (c == '%') {
            ctx->pos++;
            long d = arith_primary(ctx);
            v = d ? v % d : 0;
        } else break;
    }
    return v;
}

static long arith_add(ArithCtx *ctx) {
    long v = arith_mul(ctx);
    for (;;) {
        arith_skip_ws(ctx);
        char c = ctx->src[ctx->pos];
        if (c == '+' && ctx->src[ctx->pos+1] != '+' && ctx->src[ctx->pos+1] != '=') {
            ctx->pos++; v += arith_mul(ctx);
        } else if (c == '-' && ctx->src[ctx->pos+1] != '-' && ctx->src[ctx->pos+1] != '=') {
            ctx->pos++; v -= arith_mul(ctx);
        } else break;
    }
    return v;
}

static long arith_shift(ArithCtx *ctx) {
    long v = arith_add(ctx);
    for (;;) {
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == '<' && ctx->src[ctx->pos+1] == '<') {
            ctx->pos += 2; v <<= arith_add(ctx);
        } else if (ctx->src[ctx->pos] == '>' && ctx->src[ctx->pos+1] == '>') {
            ctx->pos += 2; v >>= arith_add(ctx);
        } else break;
    }
    return v;
}

static long arith_rel(ArithCtx *ctx) {
    long v = arith_shift(ctx);
    for (;;) {
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == '<' && ctx->src[ctx->pos+1] == '=') {
            ctx->pos += 2; v = v <= arith_shift(ctx);
        } else if (ctx->src[ctx->pos] == '>' && ctx->src[ctx->pos+1] == '=') {
            ctx->pos += 2; v = v >= arith_shift(ctx);
        } else if (ctx->src[ctx->pos] == '<' && ctx->src[ctx->pos+1] != '<') {
            ctx->pos++; v = v < arith_shift(ctx);
        } else if (ctx->src[ctx->pos] == '>' && ctx->src[ctx->pos+1] != '>') {
            ctx->pos++; v = v > arith_shift(ctx);
        } else break;
    }
    return v;
}

static long arith_eq(ArithCtx *ctx) {
    long v = arith_rel(ctx);
    for (;;) {
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == '=' && ctx->src[ctx->pos+1] == '=') {
            ctx->pos += 2; v = v == arith_rel(ctx);
        } else if (ctx->src[ctx->pos] == '!' && ctx->src[ctx->pos+1] == '=') {
            ctx->pos += 2; v = v != arith_rel(ctx);
        } else break;
    }
    return v;
}

static long arith_band(ArithCtx *ctx) {
    long v = arith_eq(ctx);
    while (1) {
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == '&' && ctx->src[ctx->pos+1] != '&') {
            ctx->pos++; v &= arith_eq(ctx);
        } else break;
    }
    return v;
}

static long arith_bxor(ArithCtx *ctx) {
    long v = arith_band(ctx);
    while (ctx->src[ctx->pos] == '^') { ctx->pos++; v ^= arith_band(ctx); }
    return v;
}

static long arith_bor(ArithCtx *ctx) {
    long v = arith_bxor(ctx);
    while (1) {
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == '|' && ctx->src[ctx->pos+1] != '|') {
            ctx->pos++; v |= arith_bxor(ctx);
        } else break;
    }
    return v;
}

static long arith_land(ArithCtx *ctx) {
    long v = arith_bor(ctx);
    while (1) {
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == '&' && ctx->src[ctx->pos+1] == '&') {
            ctx->pos += 2; long r = arith_bor(ctx); v = v && r;
        } else break;
    }
    return v;
}

static long arith_lor(ArithCtx *ctx) {
    long v = arith_land(ctx);
    while (1) {
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == '|' && ctx->src[ctx->pos+1] == '|') {
            ctx->pos += 2; long r = arith_land(ctx); v = v || r;
        } else break;
    }
    return v;
}

static long arith_ternary(ArithCtx *ctx) {
    long v = arith_lor(ctx);
    arith_skip_ws(ctx);
    if (ctx->src[ctx->pos] == '?') {
        ctx->pos++;
        long a = arith_expr(ctx);
        arith_skip_ws(ctx);
        if (ctx->src[ctx->pos] == ':') ctx->pos++;
        long b = arith_expr(ctx);
        return v ? a : b;
    }
    return v;
}

static long arith_expr(ArithCtx *ctx) {
    long v = arith_ternary(ctx);
    arith_skip_ws(ctx);
    if (ctx->src[ctx->pos] == ',') {
        ctx->pos++;
        v = arith_expr(ctx);
    }
    return v;
}

long rt_arith_eval(const char *expr) {
    if (!expr || !*expr) return 0;
    ArithCtx ctx = { expr, 0 };
    return arith_expr(&ctx);
}

char *rt_arith_str(const char *expr) {
    return rt_itoa(rt_arith_eval(expr));
}

/* ================================================================
 *  Glob Expansion
 * ================================================================ */

char **rt_glob_expand(const char *pattern, int *out_count) {
    glob_t gl;
    int ret = glob(pattern, GLOB_NOCHECK | GLOB_TILDE, NULL, &gl);
    if (ret != 0) {
        *out_count = 1;
        char **r = (char **)malloc(sizeof(char *));
        r[0] = strdup(pattern);
        return r;
    }
    *out_count = (int)gl.gl_pathc;
    char **r = (char **)malloc(sizeof(char *) * gl.gl_pathc);
    for (size_t i = 0; i < gl.gl_pathc; i++)
        r[i] = strdup(gl.gl_pathv[i]);
    globfree(&gl);
    return r;
}

void rt_glob_free(char **results, int count) {
    for (int i = 0; i < count; i++) free(results[i]);
    free(results);
}

/* ================================================================
 *  Arrays
 * ================================================================ */

static ArrayEntry *find_array(const char *name) {
    unsigned int h = hash_name(name);
    for (ArrayEntry *a = rt.arrays[h]; a; a = a->next)
        if (strcmp(a->name, name) == 0) return a;
    return NULL;
}

static ArrayEntry *ensure_array(const char *name) {
    ArrayEntry *a = find_array(name);
    if (a) return a;
    unsigned int h = hash_name(name);
    a = (ArrayEntry *)calloc(1, sizeof(ArrayEntry));
    a->name = strdup(name);
    a->cap  = 8;
    a->len  = 0;
    a->elements = (char **)calloc((size_t)a->cap, sizeof(char *));
    a->next = rt.arrays[h];
    rt.arrays[h] = a;
    return a;
}

static void array_grow(ArrayEntry *a, int need) {
    if (need < a->cap) return;
    int newcap = a->cap;
    while (newcap <= need) newcap *= 2;
    a->elements = (char **)realloc(a->elements, (size_t)newcap * sizeof(char *));
    for (int i = a->cap; i < newcap; i++) a->elements[i] = NULL;
    a->cap = newcap;
}

void rt_array_set(const char *name, int index, const char *value) {
    if (index < 0) return;
    ArrayEntry *a = ensure_array(name);
    array_grow(a, index + 1);
    free(a->elements[index]);
    a->elements[index] = strdup(value ? value : "");
    if (index >= a->len) a->len = index + 1;
}

const char *rt_array_get(const char *name, int index) {
    ArrayEntry *a = find_array(name);
    if (!a || index < 0 || index >= a->len || !a->elements[index])
        return "";
    return a->elements[index];
}

int rt_array_len(const char *name) {
    ArrayEntry *a = find_array(name);
    if (!a) return 0;
    int count = 0;
    for (int i = 0; i < a->len; i++)
        if (a->elements[i]) count++;
    return count;
}

int rt_array_max_index(const char *name) {
    ArrayEntry *a = find_array(name);
    return a ? a->len : 0;
}

void rt_array_set_list(const char *name, int count, char **values) {
    ArrayEntry *a = ensure_array(name);
    /* clear existing elements */
    for (int i = 0; i < a->len; i++) { free(a->elements[i]); a->elements[i] = NULL; }
    array_grow(a, count);
    for (int i = 0; i < count; i++)
        a->elements[i] = strdup(values[i] ? values[i] : "");
    a->len = count;
}

void rt_array_append(const char *name, const char *value) {
    ArrayEntry *a = ensure_array(name);
    array_grow(a, a->len + 1);
    a->elements[a->len] = strdup(value ? value : "");
    a->len++;
}

void rt_array_unset(const char *name) {
    unsigned int h = hash_name(name);
    ArrayEntry **pp = &rt.arrays[h];
    while (*pp) {
        if (strcmp((*pp)->name, name) == 0) {
            ArrayEntry *a = *pp;
            *pp = a->next;
            for (int i = 0; i < a->len; i++) free(a->elements[i]);
            free(a->elements);
            free(a->name);
            free(a);
            return;
        }
        pp = &(*pp)->next;
    }
}

char *rt_array_join(const char *name, const char *sep) {
    ArrayEntry *a = find_array(name);
    BStr s = bstr_new();
    if (a) {
        int first = 1;
        for (int i = 0; i < a->len; i++) {
            if (!a->elements[i]) continue;
            if (!first) bstr_append(&s, sep);
            bstr_append(&s, a->elements[i]);
            first = 0;
        }
    }
    return bstr_release(&s);
}

char **rt_array_get_all(const char *name, int *out_count) {
    ArrayEntry *a = find_array(name);
    if (!a || a->len == 0) {
        *out_count = 0;
        return NULL;
    }
    /* count set elements */
    int count = 0;
    for (int i = 0; i < a->len; i++)
        if (a->elements[i]) count++;
    if (count == 0) { *out_count = 0; return NULL; }

    char **result = (char **)malloc(sizeof(char *) * (size_t)count);
    int j = 0;
    for (int i = 0; i < a->len; i++)
        if (a->elements[i]) result[j++] = a->elements[i]; /* NOT duped – caller must not free strings */
    *out_count = count;
    return result;
}

/* ================================================================
 *  Word Splitting (by IFS)
 * ================================================================ */

char **rt_split_words(const char *str, int *out_count) {
    if (!str || !*str) {
        *out_count = 0;
        return NULL;
    }

    const char *ifs = rt_get_var("IFS");
    if (!*ifs) ifs = " \t\n";

    /* count passes: first count words, then extract */
    int cap = 16;
    int count = 0;
    char **result = (char **)malloc(sizeof(char *) * (size_t)cap);

    const char *p = str;
    /* skip leading IFS */
    while (*p && strchr(ifs, *p)) p++;

    while (*p) {
        const char *start = p;
        while (*p && !strchr(ifs, *p)) p++;
        size_t len = (size_t)(p - start);
        if (len > 0) {
            if (count >= cap) {
                cap *= 2;
                result = (char **)realloc(result, sizeof(char *) * (size_t)cap);
            }
            result[count] = (char *)malloc(len + 1);
            memcpy(result[count], start, len);
            result[count][len] = '\0';
            count++;
        }
        while (*p && strchr(ifs, *p)) p++;
    }

    *out_count = count;
    if (count == 0) { free(result); return NULL; }
    return result;
}

void rt_split_free(char **words, int count) {
    if (!words) return;
    for (int i = 0; i < count; i++) free(words[i]);
    free(words);
}

/* ================================================================
 *  Background Execution
 * ================================================================ */

int rt_exec_background(char **argv) {
    if (!argv || !argv[0]) { return 0; }

    /* reap any finished background children to prevent zombie build-up */
    while (waitpid(-1, NULL, WNOHANG) > 0) {}

    rt_sync_env();

    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 1; }
    if (pid == 0) {
        /* child – exec immediately */
        execvp(argv[0], argv);
        fprintf(stderr, "%s: command not found\n", argv[0]);
        _exit(127);
    }
    /* parent – record PID, don't wait */
    rt.last_bg_pid = pid;
    rt.last_exit = 0;
    return 0;
}

int rt_exec_background_redir(char **argv,
                              const char *in_file,
                              const char *out_file, int out_append,
                              const char *err_file, int err_append)
{
    if (!argv || !argv[0]) { return 0; }

    rt_sync_env();

    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 1; }
    if (pid == 0) {
        apply_redir_child(STDIN_FILENO,  in_file,  0);
        apply_redir_child(STDOUT_FILENO, out_file, out_append);
        apply_redir_child(STDERR_FILENO, err_file, err_append);
        execvp(argv[0], argv);
        fprintf(stderr, "%s: command not found\n", argv[0]);
        _exit(127);
    }
    rt.last_bg_pid = pid;
    rt.last_exit = 0;
    return 0;
}

/* ================================================================
 *  Builtins
 * ================================================================ */

static void echo_print_escaped(const char *s) {
    for (; *s; s++) {
        if (*s == '\\' && s[1]) {
            s++;
            switch (*s) {
                case 'n':  putchar('\n'); break;
                case 't':  putchar('\t'); break;
                case 'r':  putchar('\r'); break;
                case 'a':  putchar('\a'); break;
                case 'b':  putchar('\b'); break;
                case 'f':  putchar('\f'); break;
                case 'v':  putchar('\v'); break;
                case '\\': putchar('\\'); break;
                case '0': {
                    unsigned char v = 0;
                    for (int k = 0; k < 3 && s[1] >= '0' && s[1] <= '7'; k++)
                        v = v * 8 + (unsigned char)(*++s - '0');
                    putchar(v);
                    break;
                }
                case '3': case '1': case '2': {
                    /* octal without leading 0 */
                    unsigned char v = (unsigned char)(*s - '0');
                    for (int k = 0; k < 2 && s[1] >= '0' && s[1] <= '7'; k++)
                        v = v * 8 + (unsigned char)(*++s - '0');
                    putchar(v);
                    break;
                }
                case 'x': {
                    unsigned char v = 0;
                    for (int k = 0; k < 2 && isxdigit((unsigned char)s[1]); k++) {
                        s++;
                        v = v * 16 + (unsigned char)(isdigit((unsigned char)*s)
                            ? *s - '0'
                            : (tolower((unsigned char)*s) - 'a' + 10));
                    }
                    putchar(v);
                    break;
                }
                default: putchar('\\'); putchar(*s); break;
            }
        } else {
            putchar(*s);
        }
    }
}

int rt_builtin_echo(int argc, char **argv) {
    int newline = 1;
    int escape = 0;
    int start = 1;

    /* parse flags: -n, -e, -ne, -en, -neE etc. */
    while (start < argc && argv[start][0] == '-' && argv[start][1]) {
        const char *f = argv[start] + 1;
        int valid = 1;
        for (; *f; f++) {
            if (*f != 'n' && *f != 'e' && *f != 'E') { valid = 0; break; }
        }
        if (!valid) break;
        f = argv[start] + 1;
        for (; *f; f++) {
            if (*f == 'n') newline = 0;
            if (*f == 'e') escape = 1;
            if (*f == 'E') escape = 0;
        }
        start++;
    }

    for (int i = start; i < argc; i++) {
        if (i > start) putchar(' ');
        if (escape)
            echo_print_escaped(argv[i]);
        else
            fputs(argv[i], stdout);
    }
    if (newline) putchar('\n');
    fflush(stdout);
    return 0;
}

int rt_builtin_printf_cmd(int argc, char **argv) {
    if (argc < 2) return 1;

    int start = 1;
    const char *var_name = NULL;  /* for -v varname */

    /* parse -v varname */
    if (argc > 2 && strcmp(argv[1], "-v") == 0) {
        var_name = argv[2];
        start = 3;
    }
    if (start >= argc) return 1;

    const char *fmt = argv[start];
    int ai = start + 1; /* argument index */

    /* If -v, capture output to a temp file and read back */
    FILE *out = stdout;
    char *tmpfile_path = NULL;
    if (var_name) {
        tmpfile_path = strdup("/tmp/.bash2c_printf_XXXXXX");
        int tfd = mkstemp(tmpfile_path);
        if (tfd >= 0) {
            out = fdopen(tfd, "w+");
        }
    }

    for (const char *p = fmt; *p; p++) {
        if (*p == '\\') {
            p++;
            switch (*p) {
                case 'n':  fputc('\n', out); break;
                case 't':  fputc('\t', out); break;
                case 'r':  fputc('\r', out); break;
                case 'a':  fputc('\a', out); break;
                case 'b':  fputc('\b', out); break;
                case '\\': fputc('\\', out); break;
                case '0': case '1': case '2': case '3': {
                    unsigned char v = 0;
                    for (int k = 0; k < 3 && *p >= '0' && *p <= '7'; k++, p++)
                        v = v * 8 + (unsigned char)(*p - '0');
                    p--;
                    fputc(v, out);
                    break;
                }
                default: fputc('\\', out); fputc(*p, out); break;
            }
        } else if (*p == '%') {
            /* Extract full format spec: %[flags][width][.precision]type */
            char spec[64];
            int si = 0;
            spec[si++] = '%';
            p++;

            if (*p == '%') { fputc('%', out); continue; }

            /* flags: -, +, space, 0, # */
            while (*p && strchr("-+ 0#", *p) && si < 60) spec[si++] = *p++;
            /* width */
            if (*p == '*') {
                int w = ai < argc ? (int)strtol(argv[ai++], NULL, 10) : 0;
                si += snprintf(spec + si, sizeof(spec) - (size_t)si, "%d", w);
                p++;
            } else {
                while (*p && isdigit((unsigned char)*p) && si < 60) spec[si++] = *p++;
            }
            /* precision */
            if (*p == '.') {
                spec[si++] = *p++;
                if (*p == '*') {
                    int pr = ai < argc ? (int)strtol(argv[ai++], NULL, 10) : 0;
                    si += snprintf(spec + si, sizeof(spec) - (size_t)si, "%d", pr);
                    p++;
                } else {
                    while (*p && isdigit((unsigned char)*p) && si < 60) spec[si++] = *p++;
                }
            }

            char type = *p;
            if (!type) break;

            if (type == 's') {
                spec[si++] = 's'; spec[si] = '\0';
                fprintf(out, spec, ai < argc ? argv[ai++] : "");
            } else if (type == 'b') {
                /* %b: like %s but interpret backslash escapes */
                spec[si++] = 's'; spec[si] = '\0';
                const char *arg = ai < argc ? argv[ai++] : "";
                /* expand escapes into a temp buffer */
                BStr tmp = bstr_new();
                for (const char *q = arg; *q; q++) {
                    if (*q == '\\' && q[1]) {
                        q++;
                        switch (*q) {
                            case 'n': bstr_append_char(&tmp, '\n'); break;
                            case 't': bstr_append_char(&tmp, '\t'); break;
                            case 'r': bstr_append_char(&tmp, '\r'); break;
                            case 'a': bstr_append_char(&tmp, '\a'); break;
                            case '\\': bstr_append_char(&tmp, '\\'); break;
                            case '0': {
                                unsigned char v = 0;
                                for (int k = 0; k < 3 && q[1] >= '0' && q[1] <= '7'; k++)
                                    v = v * 8 + (unsigned char)(*++q - '0');
                                bstr_append_char(&tmp, (char)v);
                                break;
                            }
                            default: bstr_append_char(&tmp, '\\'); bstr_append_char(&tmp, *q); break;
                        }
                    } else {
                        bstr_append_char(&tmp, *q);
                    }
                }
                char *expanded = bstr_release(&tmp);
                fprintf(out, spec, expanded);
                free(expanded);
            } else if (type == 'd' || type == 'i') {
                spec[si++] = 'l'; spec[si++] = 'd'; spec[si] = '\0';
                long v = ai < argc ? strtol(argv[ai++], NULL, 10) : 0;
                fprintf(out, spec, v);
            } else if (type == 'f') {
                spec[si++] = 'f'; spec[si] = '\0';
                double v = ai < argc ? strtod(argv[ai++], NULL) : 0.0;
                fprintf(out, spec, v);
            } else if (type == 'x' || type == 'X' || type == 'o' || type == 'u') {
                spec[si++] = 'l'; spec[si++] = type; spec[si] = '\0';
                unsigned long v = ai < argc ? strtoul(argv[ai++], NULL, 10) : 0;
                fprintf(out, spec, v);
            } else if (type == 'c') {
                const char *arg = ai < argc ? argv[ai++] : "";
                fputc(arg[0] ? arg[0] : '\0', out);
            } else {
                /* unknown — pass through */
                spec[si++] = type; spec[si] = '\0';
                fputs(spec, out);
            }
        } else {
            fputc(*p, out);
        }
    }

    if (var_name && out != stdout) {
        fflush(out);
        /* read back from tmpfile */
        rewind(out);
        BStr result = bstr_new();
        char buf[4096];
        while (fgets(buf, sizeof(buf), out)) bstr_append(&result, buf);
        fclose(out);
        if (tmpfile_path) { unlink(tmpfile_path); free(tmpfile_path); }
        char *val = bstr_release(&result);
        rt_set_var(var_name, val);
        free(val);
    } else {
        fflush(out);
    }
    return 0;
}

int rt_builtin_cd(int argc, char **argv) {
    const char *dir = argc > 1 ? argv[1] : getenv("HOME");
    if (!dir) { fprintf(stderr, "cd: HOME not set\n"); return 1; }
    if (chdir(dir) < 0) {
        fprintf(stderr, "cd: %s: %s\n", dir, strerror(errno));
        return 1;
    }
    /* update PWD */
    char cwd[4096];
    if (getcwd(cwd, sizeof(cwd))) rt_set_var("PWD", cwd);
    return 0;
}

int rt_builtin_exit(int argc, char **argv) {
    int code = argc > 1 ? (int)strtol(argv[1], NULL, 10) : rt.last_exit;
    rt_cleanup();
    exit(code);
    return code; /* unreachable */
}

int rt_builtin_read(int argc, char **argv) {
    int raw = 0;
    const char *prompt = NULL;
    int i = 1;
    while (i < argc && argv[i][0] == '-') {
        if (strcmp(argv[i], "-r") == 0) { raw = 1; i++; }
        else if (strcmp(argv[i], "-p") == 0 && i + 1 < argc) {
            prompt = argv[i + 1]; i += 2;
        } else { i++; }
    }
    if (prompt) { fputs(prompt, stderr); fflush(stderr); }

    BStr line = bstr_new();
    int ch;
    while ((ch = getchar()) != EOF && ch != '\n') {
        if (!raw && ch == '\\') {
            ch = getchar();
            if (ch == EOF) break;
            if (ch == '\n') continue; /* line continuation */
        }
        bstr_append_char(&line, (char)ch);
    }
    if (ch == EOF && line.len == 0) { bstr_free(&line); return 1; }

    char *val = bstr_release(&line);

    if (i < argc) {
        /* split by IFS into variables */
        const char *ifs = rt_get_var("IFS");
        if (!*ifs) ifs = " \t\n";

        char *p = val;
        for (int vi = i; vi < argc; vi++) {
            /* skip leading IFS chars */
            while (*p && strchr(ifs, *p)) p++;
            if (vi == argc - 1) {
                /* last variable gets the rest */
                /* trim trailing IFS */
                char *end = p + strlen(p);
                while (end > p && strchr(ifs, *(end-1))) end--;
                *end = '\0';
                rt_set_var(argv[vi], p);
            } else {
                char *start = p;
                while (*p && !strchr(ifs, *p)) p++;
                char saved = *p;
                if (*p) *p = '\0';
                rt_set_var(argv[vi], start);
                if (saved) p++;
            }
        }
    } else {
        rt_set_var("REPLY", val);
    }
    free(val);
    return 0;
}

int rt_builtin_export(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        char *eq = strchr(argv[i], '=');
        if (eq) {
            size_t nlen = (size_t)(eq - argv[i]);
            char *name = (char *)malloc(nlen + 1);
            memcpy(name, argv[i], nlen);
            name[nlen] = '\0';
            rt_set_var(name, eq + 1);
            rt_export_var(name);
            free(name);
        } else {
            rt_export_var(argv[i]);
        }
    }
    return 0;
}

/* test / [ builtin */
static int test_primary(int argc, char **argv, int *pos);

static int test_expr(int argc, char **argv, int *pos) {
    int v = test_primary(argc, argv, pos);
    while (*pos < argc) {
        if (strcmp(argv[*pos], "-a") == 0) {
            (*pos)++;
            int r = test_primary(argc, argv, pos);
            v = v && r;
        } else if (strcmp(argv[*pos], "-o") == 0) {
            (*pos)++;
            int r = test_primary(argc, argv, pos);
            v = v || r;
        } else break;
    }
    return v;
}

static int test_primary(int argc, char **argv, int *pos) {
    if (*pos >= argc) return 0;

    /* ! expr */
    if (strcmp(argv[*pos], "!") == 0) {
        (*pos)++;
        return !test_primary(argc, argv, pos);
    }
    /* ( expr ) */
    if (strcmp(argv[*pos], "(") == 0) {
        (*pos)++;
        int v = test_expr(argc, argv, pos);
        if (*pos < argc && strcmp(argv[*pos], ")") == 0) (*pos)++;
        return v;
    }
    /* unary operators */
    if (argv[*pos][0] == '-' && strlen(argv[*pos]) == 2 && *pos + 1 < argc) {
        char op = argv[*pos][1];
        const char *arg = argv[*pos + 1];

        /* check if this is a binary op (next next token exists) */
        if (*pos + 2 < argc) {
            const char *mid = argv[*pos + 1];
            /* binary operators: check if mid is a binary op */
            if (strcmp(mid, "=") == 0 || strcmp(mid, "!=") == 0 ||
                strcmp(mid, "-eq") == 0 || strcmp(mid, "-ne") == 0 ||
                strcmp(mid, "-lt") == 0 || strcmp(mid, "-le") == 0 ||
                strcmp(mid, "-gt") == 0 || strcmp(mid, "-ge") == 0) {
                goto binary_op;
            }
        }

        /* file tests */
        switch (op) {
            case 'e': (*pos) += 2; return access(arg, F_OK) == 0;
            case 'f': (*pos) += 2; { struct stat st; return stat(arg, &st) == 0 && S_ISREG(st.st_mode); }
            case 'd': (*pos) += 2; { struct stat st; return stat(arg, &st) == 0 && S_ISDIR(st.st_mode); }
            case 'r': (*pos) += 2; return access(arg, R_OK) == 0;
            case 'w': (*pos) += 2; return access(arg, W_OK) == 0;
            case 'x': (*pos) += 2; return access(arg, X_OK) == 0;
            case 's': (*pos) += 2; { struct stat st; return stat(arg, &st) == 0 && st.st_size > 0; }
            case 'L': case 'h': (*pos) += 2; { struct stat st; return lstat(arg, &st) == 0 && S_ISLNK(st.st_mode); }
            case 'n': (*pos) += 2; return strlen(arg) > 0;
            case 'z': (*pos) += 2; return strlen(arg) == 0;
        }
    }

binary_op:
    /* binary operators */
    if (*pos + 2 < argc) {
        const char *left = argv[*pos];
        const char *op   = argv[*pos + 1];
        const char *right = argv[*pos + 2];

        if (strcmp(op, "=") == 0 || strcmp(op, "==") == 0) {
            *pos += 3; return strcmp(left, right) == 0;
        }
        if (strcmp(op, "!=") == 0) {
            *pos += 3; return strcmp(left, right) != 0;
        }
        if (strcmp(op, "-eq") == 0) {
            *pos += 3; return strtol(left, NULL, 10) == strtol(right, NULL, 10);
        }
        if (strcmp(op, "-ne") == 0) {
            *pos += 3; return strtol(left, NULL, 10) != strtol(right, NULL, 10);
        }
        if (strcmp(op, "-lt") == 0) {
            *pos += 3; return strtol(left, NULL, 10) < strtol(right, NULL, 10);
        }
        if (strcmp(op, "-le") == 0) {
            *pos += 3; return strtol(left, NULL, 10) <= strtol(right, NULL, 10);
        }
        if (strcmp(op, "-gt") == 0) {
            *pos += 3; return strtol(left, NULL, 10) > strtol(right, NULL, 10);
        }
        if (strcmp(op, "-ge") == 0) {
            *pos += 3; return strtol(left, NULL, 10) >= strtol(right, NULL, 10);
        }
    }

    /* single string: true if non-empty */
    {
        int v = strlen(argv[*pos]) > 0;
        (*pos)++;
        return v;
    }
}

int rt_builtin_test(int argc, char **argv) {
    /* if invoked as '[', strip trailing ']' */
    int end = argc;
    if (strcmp(argv[0], "[") == 0) {
        if (argc > 1 && strcmp(argv[argc - 1], "]") == 0) end--;
        else { fprintf(stderr, "[: missing ']'\n"); return 2; }
    }
    if (end <= 1) return 1; /* no args = false */

    int pos = 1;
    int result = test_expr(end, argv, &pos);
    return result ? 0 : 1;
}

int rt_builtin_true_cmd(int argc, char **argv) {
    (void)argc; (void)argv;
    return 0;
}

int rt_builtin_false_cmd(int argc, char **argv) {
    (void)argc; (void)argv;
    return 1;
}

int rt_builtin_return_cmd(int argc, char **argv) {
    rt.func_returning = 1;
    rt.last_exit = argc > 1 ? (int)strtol(argv[1], NULL, 10) : 0;
    return rt.last_exit;
}

int rt_builtin_local_cmd(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        char *eq = strchr(argv[i], '=');
        if (eq) {
            size_t nlen = (size_t)(eq - argv[i]);
            char *name = (char *)malloc(nlen + 1);
            memcpy(name, argv[i], nlen);
            name[nlen] = '\0';
            rt_set_local(name, eq + 1);
            free(name);
        } else {
            rt_set_local(argv[i], "");
        }
    }
    return 0;
}

int rt_builtin_shift_cmd(int argc, char **argv) {
    int n = argc > 1 ? (int)strtol(argv[1], NULL, 10) : 1;
    rt_shift_args(n);
    return 0;
}

int rt_builtin_wait_cmd(int argc, char **argv) {
    if (argc > 1) {
        /* wait for a specific PID */
        pid_t pid = (pid_t)strtol(argv[1], NULL, 10);
        int status;
        if (waitpid(pid, &status, 0) < 0) {
            /* already reaped or no such child */
            return 127;
        }
        rt.last_exit = WIFEXITED(status) ? WEXITSTATUS(status) : 128;
    } else {
        /* wait for all children */
        int status;
        while (waitpid(-1, &status, 0) > 0) {
            rt.last_exit = WIFEXITED(status) ? WEXITSTATUS(status) : 128;
        }
    }
    return rt.last_exit;
}

int rt_builtin_unset_cmd(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        /* skip flags like -v, -f */
        if (argv[i][0] == '-') continue;
        rt_unset_var(argv[i]);
        rt_array_unset(argv[i]);
    }
    return 0;
}

/* ======================== Builtin Lookup Table ======================== */

typedef struct {
    const char *name;
    BuiltinFunc func;
} BuiltinEntry;

static const BuiltinEntry builtins[] = {
    { "echo",    rt_builtin_echo       },
    { "printf",  rt_builtin_printf_cmd },
    { "cd",      rt_builtin_cd         },
    { "exit",    rt_builtin_exit       },
    { "read",    rt_builtin_read       },
    { "export",  rt_builtin_export     },
    { "test",    rt_builtin_test       },
    { "[",       rt_builtin_test       },
    { "true",    rt_builtin_true_cmd   },
    { "false",   rt_builtin_false_cmd  },
    { "return",  rt_builtin_return_cmd },
    { "local",   rt_builtin_local_cmd  },
    { "shift",   rt_builtin_shift_cmd  },
    { "wait",    rt_builtin_wait_cmd   },
    { "unset",   rt_builtin_unset_cmd  },
    { ":",       rt_builtin_true_cmd   }, /* colon = noop */
    { NULL, NULL }
};

BuiltinFunc rt_find_builtin(const char *name) {
    for (int i = 0; builtins[i].name; i++)
        if (strcmp(builtins[i].name, name) == 0) return builtins[i].func;
    return NULL;
}

int rt_is_true(void) { return rt.last_exit == 0; }

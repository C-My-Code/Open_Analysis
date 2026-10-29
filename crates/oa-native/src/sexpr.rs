// SPDX-FileCopyrightText: 2026 Open_Analysis contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//! KiCad S-expression tokenizer and tree builder.
//!
//! Mirrors `oa_ingest_kicad.sexpr.parse` token for token, including the quirks of its regular
//! expression: an unterminated quote is skipped, and `(pts (xy x y) ...)` lists made only of
//! `xy` points become `(N, 2)` float64 arrays. Any input the Python oracle would reject
//! (unbalanced parens, no root node, a non-numeric `xy` value) returns `None`, and the caller
//! re-parses in Python so that the error is the oracle's own.

use numpy::{PyArray1, PyArrayMethods};
use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

/// Python `str.isspace` (the regex `\s` for `str` patterns).
#[inline]
fn is_space(c: char) -> bool {
    matches!(
        c,
        '\t' | '\n'
            | '\x0b'
            | '\x0c'
            | '\r'
            | '\x1c'..='\x1f'
            | ' '
            | '\u{85}'
            | '\u{a0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}'
            | '\u{2028}'
            | '\u{2029}'
            | '\u{202f}'
            | '\u{205f}'
            | '\u{3000}'
    )
}

struct Cur<'a> {
    s: &'a str,
    i: usize,
}

impl<'a> Cur<'a> {
    #[inline]
    fn peek(&self) -> Option<char> {
        self.s[self.i..].chars().next()
    }
    fn skip_ws(&mut self) {
        while let Some(c) = self.peek() {
            if !is_space(c) {
                break;
            }
            self.i += c.len_utf8();
        }
    }
    /// `\s+`: true when at least one whitespace char was consumed.
    fn ws1(&mut self) -> bool {
        let start = self.i;
        self.skip_ws();
        self.i > start
    }
    /// `[^\s()]+`
    fn num(&mut self) -> Option<&'a str> {
        let start = self.i;
        while let Some(c) = self.peek() {
            if is_space(c) || c == '(' || c == ')' {
                break;
            }
            self.i += c.len_utf8();
        }
        if self.i > start {
            Some(&self.s[start..self.i])
        } else {
            None
        }
    }
    fn eat(&mut self, lit: &str) -> bool {
        if self.s[self.i..].starts_with(lit) {
            self.i += lit.len();
            true
        } else {
            false
        }
    }
}

/// Outcome of the fast-path attempt at a `(` token.
enum Pts {
    /// Not an all-xy point list: the `(` is an ordinary open paren.
    No,
    /// Matched; end offset and the values `x0 y0 x1 y1 ...`.
    Yes(usize, Vec<f64>),
    /// Matched but a value is not a float (numpy would raise).
    Bad,
}

/// `\(pts(?:\s*\(xy\s+NUM\s+NUM\s*\))+\s*\)` anchored at `start`.
fn try_pts(s: &str, start: usize) -> Pts {
    let mut c = Cur { s, i: start };
    if !c.eat("(pts") {
        return Pts::No;
    }
    let mut vals: Vec<f64> = Vec::new();
    let mut bad = false;
    let mut count = 0usize;
    loop {
        let save = c.i;
        c.skip_ws();
        if !c.eat("(xy") || !c.ws1() {
            c.i = save;
            break;
        }
        let Some(a) = c.num() else {
            c.i = save;
            break;
        };
        if !c.ws1() {
            c.i = save;
            break;
        }
        let Some(b) = c.num() else {
            c.i = save;
            break;
        };
        c.skip_ws();
        if !c.eat(")") {
            c.i = save;
            break;
        }
        count += 1;
        match (a.parse::<f64>(), b.parse::<f64>()) {
            (Ok(x), Ok(y)) => {
                vals.push(x);
                vals.push(y);
            }
            _ => bad = true,
        }
    }
    if count == 0 {
        return Pts::No;
    }
    c.skip_ws();
    if !c.eat(")") {
        // the regex would backtrack to fewer repetitions, but each one ends in `)` and the
        // next thing is then `\s*(xy`, never `)`, so there is no match
        return Pts::No;
    }
    if bad {
        Pts::Bad
    } else {
        Pts::Yes(c.i, vals)
    }
}

fn unescape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c == '\\' {
            match it.next() {
                Some(n) => out.push(n),
                None => out.push('\\'),
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// Parse `text`; `None` means "let the Python oracle handle (and report) it".
#[pyfunction]
pub fn parse<'py>(py: Python<'py>, text: &str) -> PyResult<Option<Bound<'py, PyList>>> {
    let bytes = text.as_bytes();
    let n = bytes.len();
    let root = PyList::empty(py);
    let mut stack: Vec<Bound<'py, PyList>> = Vec::new();
    let mut cur = root.clone();
    let mut i = 0usize;
    while i < n {
        let b = bytes[i];
        match b {
            b'(' => match try_pts(text, i) {
                Pts::Yes(end, vals) => {
                    let rows = vals.len() / 2;
                    let arr = PyArray1::from_vec(py, vals).reshape([rows, 2])?;
                    let node = PyList::empty(py);
                    node.append("pts")?;
                    node.append(arr)?;
                    cur.append(node)?;
                    i = end;
                }
                Pts::Bad => return Ok(None),
                Pts::No => {
                    let node = PyList::empty(py);
                    cur.append(&node)?;
                    stack.push(cur);
                    cur = node;
                    i += 1;
                }
            },
            b')' => {
                match stack.pop() {
                    Some(p) => cur = p,
                    None => return Ok(None),
                }
                i += 1;
            }
            b'"' => {
                // `"(?:[^"\\]|\\.)*"`, where `.` excludes only "\n"
                let mut j = i + 1;
                let mut ok = false;
                let mut has_esc = false;
                while j < n {
                    match bytes[j] {
                        b'"' => {
                            ok = true;
                            break;
                        }
                        b'\\' => {
                            has_esc = true;
                            if j + 1 >= n || bytes[j + 1] == b'\n' {
                                break;
                            }
                            // the escaped char may be multi-byte; its continuation bytes are
                            // never `"` or `\`, so advancing past the lead byte is enough
                            j += 2;
                        }
                        _ => j += 1,
                    }
                }
                if ok {
                    let inner = &text[i + 1..j];
                    if has_esc {
                        cur.append(PyString::new(py, &unescape(inner)))?;
                    } else {
                        cur.append(PyString::new(py, inner))?;
                    }
                    i = j + 1;
                } else {
                    i += 1; // finditer skips a character no alternative matches
                }
            }
            _ => {
                // whitespace is skipped, anything else is an atom `[^\s()"]+`
                let ch = text[i..].chars().next().unwrap();
                if is_space(ch) {
                    i += ch.len_utf8();
                    continue;
                }
                let start = i;
                while i < n {
                    let c = text[i..].chars().next().unwrap();
                    if is_space(c) || c == '(' || c == ')' || c == '"' {
                        break;
                    }
                    i += c.len_utf8();
                }
                cur.append(PyString::new(py, &text[start..i]))?;
            }
        }
    }
    if !stack.is_empty() || root.is_empty() {
        return Ok(None);
    }
    match root.get_item(0)?.downcast_into::<PyList>() {
        Ok(l) => Ok(Some(l)),
        Err(_) => Ok(None),
    }
}

---
title: Sample course
subtitle: "Class 07 — Ensemble Models"
author: A. Teacher
date: 2026-10-31
---

# One Model at a Time

## Where We Left Off

```{=latex}
\begin{center}
\begin{tikzpicture}
  \node[fillbox=cblue] (a) {\textbf{linear, probabilistic}\\ Classes 03--04};
  \node[fillbox=cgreen, right=8mm of a] (b) {\textbf{tree, neighbours}\\ Class 06};
\end{tikzpicture}
\end{center}
```

* Until now: **one** model, tuned
* Today: combine models

<!-- defines: ensemble, member -->

## Score Curves

![Scores of one model](plots/07/e1_scores.pdf)

::: notes
Say that one model overfits.
:::

## Bagging in Pictures

![](plots/07/e3_bagging.png)

## Boosting Sketch

![](plots/07/e4_boost.svg)

## Missing Plot

![](plots/07/not_there.pdf)

# Combining Models

## Two Columns

:::: {.columns}
::: {.column width="50%"}
* left bullet
:::
::: {.column width="50%"}
* right bullet
:::
::::

## Voting Rule

Majority vote of $m$ members:

$$\hat{y} = \operatorname{mode}\{h_1(x), \dots, h_m(x)\}$$

## Error Table

| Model | Error |
|-------|-------|
| tree  | 0.21  |
| bag   | 0.14  |

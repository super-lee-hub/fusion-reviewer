# A Game-Theoretic Model of Strategic Information Disclosure

## Abstract

We develop a formal model of voluntary information disclosure under asymmetric competition. The model extends the standard cheap-talk framework to multi-receiver settings where senders have heterogeneous preferences over audience beliefs.

## 1. Introduction

Information disclosure is a central topic in game theory and organizational economics. This paper provides a formal analysis of strategic communication when a sender faces multiple receivers with competing interests.

## 2. Model

### 2.1 Setup

Consider a game with one sender S and N >= 2 receivers R_1, ..., R_N. The state of the world theta is drawn from a uniform distribution on [0, 1]. The sender observes theta privately and chooses a message m from a rich message space M.

### 2.2 Payoffs

Receiver i chooses action a_i in R. The sender's payoff is:

U_S = -sum_{i=1}^{N} w_i (a_i - theta - b_i)^2

where w_i > 0 are weights and b_i are sender bias parameters. Each receiver i has payoff:

U_i = -(a_i - theta)^2

### 2.3 Equilibrium

We focus on perfect Bayesian equilibrium (PBE) in pure strategies. The equilibrium concept requires that: (1) the sender's message strategy maximizes expected payoff given receiver strategies, (2) each receiver's action maximizes expected payoff given posterior beliefs, and (3) posterior beliefs are derived from Bayes' rule.

## 3. Results

**Proposition 1.** In any informative equilibrium, the sender partitions the state space into at most N intervals and truthfully reveals which interval contains the true state.

**Proof.** The proof follows from Crawford and Sobel (1982). The quadratic loss structure implies that the sender's optimal strategy partitions the state space. With N receivers, the maximum number of partition elements equals the number of distinct receiver actions.

**Proposition 2.** As the number of receivers increases, the maximum amount of information transmitted in equilibrium weakly decreases if the sender's bias parameters are heterogeneous.

**Proof.** Consider the sender's indifference condition...

## 4. Conclusions

This paper provides a formal characterization of information disclosure under multi-receiver strategic competition. The results demonstrate bounds on equilibrium informativeness and conditions for full disclosure.

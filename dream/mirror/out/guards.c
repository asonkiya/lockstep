// SPDX-License-Identifier: GPL-2.0
#include <linux/build_bug.h>
#include <linux/kernel.h>
#include <linux/stddef.h>
#include <linux/clk-provider.h>
#include <linux/timecounter.h>
#include <linux/ieee80211.h>
#include <linux/fb.h>
#include <linux/ratelimit.h>

static_assert(sizeof(struct clk_div_table) == 8, "clk_div_table: size mismatch vs real kernel");
static_assert(offsetof(struct clk_div_table, val) == 0, "clk_div_table.val: offset mismatch vs real kernel");
static_assert(offsetof(struct clk_div_table, div) == 4, "clk_div_table.div: offset mismatch vs real kernel");
static_assert(sizeof(struct clk_duty) == 8, "clk_duty: size mismatch vs real kernel");
static_assert(offsetof(struct clk_duty, num) == 0, "clk_duty.num: offset mismatch vs real kernel");
static_assert(offsetof(struct clk_duty, den) == 4, "clk_duty.den: offset mismatch vs real kernel");
static_assert(sizeof(struct cyclecounter) == 24, "cyclecounter: size mismatch vs real kernel");
static_assert(offsetof(struct cyclecounter, read) == 0, "cyclecounter.read: offset mismatch vs real kernel");
static_assert(offsetof(struct cyclecounter, mask) == 8, "cyclecounter.mask: offset mismatch vs real kernel");
static_assert(offsetof(struct cyclecounter, mult) == 16, "cyclecounter.mult: offset mismatch vs real kernel");
static_assert(offsetof(struct cyclecounter, shift) == 20, "cyclecounter.shift: offset mismatch vs real kernel");
static_assert(sizeof(struct timecounter) == 40, "timecounter: size mismatch vs real kernel");
static_assert(offsetof(struct timecounter, cc) == 0, "timecounter.cc: offset mismatch vs real kernel");
static_assert(offsetof(struct timecounter, cycle_last) == 8, "timecounter.cycle_last: offset mismatch vs real kernel");
static_assert(offsetof(struct timecounter, nsec) == 16, "timecounter.nsec: offset mismatch vs real kernel");
static_assert(offsetof(struct timecounter, mask) == 24, "timecounter.mask: offset mismatch vs real kernel");
static_assert(offsetof(struct timecounter, frac) == 32, "timecounter.frac: offset mismatch vs real kernel");
static_assert(sizeof(struct ieee80211_he_mu_edca_param_ac_rec) == 3, "ieee80211_he_mu_edca_param_ac_rec: size mismatch vs real kernel");
static_assert(offsetof(struct ieee80211_he_mu_edca_param_ac_rec, aifsn) == 0, "ieee80211_he_mu_edca_param_ac_rec.aifsn: offset mismatch vs real kernel");
static_assert(offsetof(struct ieee80211_he_mu_edca_param_ac_rec, ecw_min_max) == 1, "ieee80211_he_mu_edca_param_ac_rec.ecw_min_max: offset mismatch vs real kernel");
static_assert(offsetof(struct ieee80211_he_mu_edca_param_ac_rec, mu_edca_timer) == 2, "ieee80211_he_mu_edca_param_ac_rec.mu_edca_timer: offset mismatch vs real kernel");
static_assert(sizeof(struct ieee80211_mu_edca_param_set) == 13, "ieee80211_mu_edca_param_set: size mismatch vs real kernel");
static_assert(offsetof(struct ieee80211_mu_edca_param_set, mu_qos_info) == 0, "ieee80211_mu_edca_param_set.mu_qos_info: offset mismatch vs real kernel");
static_assert(offsetof(struct ieee80211_mu_edca_param_set, ac_be) == 1, "ieee80211_mu_edca_param_set.ac_be: offset mismatch vs real kernel");
static_assert(offsetof(struct ieee80211_mu_edca_param_set, ac_bk) == 4, "ieee80211_mu_edca_param_set.ac_bk: offset mismatch vs real kernel");
static_assert(offsetof(struct ieee80211_mu_edca_param_set, ac_vi) == 7, "ieee80211_mu_edca_param_set.ac_vi: offset mismatch vs real kernel");
static_assert(offsetof(struct ieee80211_mu_edca_param_set, ac_vo) == 10, "ieee80211_mu_edca_param_set.ac_vo: offset mismatch vs real kernel");
static_assert(sizeof(struct fb_blit_caps) == 32, "fb_blit_caps: size mismatch vs real kernel");
static_assert(offsetof(struct fb_blit_caps, x) == 0, "fb_blit_caps.x: offset mismatch vs real kernel");
static_assert(offsetof(struct fb_blit_caps, y) == 8, "fb_blit_caps.y: offset mismatch vs real kernel");
static_assert(offsetof(struct fb_blit_caps, len) == 24, "fb_blit_caps.len: offset mismatch vs real kernel");
static_assert(offsetof(struct fb_blit_caps, flags) == 28, "fb_blit_caps.flags: offset mismatch vs real kernel");
static_assert(sizeof(struct ratelimit_state) == 96, "ratelimit_state: size mismatch vs real kernel");
static_assert(offsetof(struct ratelimit_state, lock) == 0, "ratelimit_state.lock: offset mismatch vs real kernel");
static_assert(offsetof(struct ratelimit_state, interval) == 64, "ratelimit_state.interval: offset mismatch vs real kernel");
static_assert(offsetof(struct ratelimit_state, burst) == 68, "ratelimit_state.burst: offset mismatch vs real kernel");
static_assert(offsetof(struct ratelimit_state, rs_n_left) == 72, "ratelimit_state.rs_n_left: offset mismatch vs real kernel");
static_assert(offsetof(struct ratelimit_state, missed) == 76, "ratelimit_state.missed: offset mismatch vs real kernel");
static_assert(offsetof(struct ratelimit_state, flags) == 80, "ratelimit_state.flags: offset mismatch vs real kernel");
static_assert(offsetof(struct ratelimit_state, begin) == 88, "ratelimit_state.begin: offset mismatch vs real kernel");

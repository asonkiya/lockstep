// SPDX-License-Identifier: GPL-2.0
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/types.h>

unsigned long lcm(unsigned long, unsigned long);
unsigned long cgir_lcm(unsigned long, unsigned long);
unsigned long lcm_not_zero(unsigned long, unsigned long);
unsigned long cgir_lcm_not_zero(unsigned long, unsigned long);
unsigned long long int_pow(unsigned long long, unsigned int);
unsigned long long cgir_int_pow(unsigned long long, unsigned int);
unsigned long gcd(unsigned long, unsigned long);
unsigned long cgir_gcd(unsigned long, unsigned long);
unsigned long int_sqrt(unsigned long);
unsigned long cgir_int_sqrt(unsigned long);
unsigned int __sw_hweight32(unsigned int);
unsigned int cgir_sw_hweight32(unsigned int);

static int __init fleet_init(void)
{
	{ unsigned long c=0,bad=0; long fb=-1; unsigned long a,b;(void)a;(void)b;
	  for(a=0;a<=400;a++)for(b=0;b<=400;b++){c++;if(cgir_lcm(a,b)!=lcm(a,b)){bad++;if(fb<0)fb=a*1000+b;}}
	  pr_emerg("FLEET_PROBE: lcm n=%lu bad=%lu firstbad=%ld verdict=%s\n", c, bad, fb, bad==0?"DIFF_PASS":"DIFF_FAIL"); }
	{ unsigned long c=0,bad=0; long fb=-1; unsigned long a,b;(void)a;(void)b;
	  for(a=0;a<=400;a++)for(b=0;b<=400;b++){c++;if(cgir_lcm_not_zero(a,b)!=lcm_not_zero(a,b)){bad++;if(fb<0)fb=a*1000+b;}}
	  pr_emerg("FLEET_PROBE: lcm_not_zero n=%lu bad=%lu firstbad=%ld verdict=%s\n", c, bad, fb, bad==0?"DIFF_PASS":"DIFF_FAIL"); }
	{ unsigned long c=0,bad=0; long fb=-1; unsigned long a,b;(void)a;(void)b;
	  {u64 bb;unsigned int e;for(bb=0;bb<=90;bb++)for(e=0;e<=14;e++){c++;if(cgir_int_pow(bb,e)!=int_pow(bb,e)){bad++;if(fb<0)fb=bb*100+e;}}}
	  pr_emerg("FLEET_PROBE: int_pow n=%lu bad=%lu firstbad=%ld verdict=%s\n", c, bad, fb, bad==0?"DIFF_PASS":"DIFF_FAIL"); }
	{ unsigned long c=0,bad=0; long fb=-1; unsigned long a,b;(void)a;(void)b;
	  for(a=0;a<=400;a++)for(b=0;b<=400;b++){c++;if(cgir_gcd(a,b)!=gcd(a,b)){bad++;if(fb<0)fb=a*1000+b;}}
	  pr_emerg("FLEET_PROBE: gcd n=%lu bad=%lu firstbad=%ld verdict=%s\n", c, bad, fb, bad==0?"DIFF_PASS":"DIFF_FAIL"); }
	{ unsigned long c=0,bad=0; long fb=-1; unsigned long a,b;(void)a;(void)b;
	  {u64 x;for(x=0;x<=40000;x++){c++;if(cgir_int_sqrt(x)!=int_sqrt(x)){bad++;if(fb<0)fb=x;}}}
	  pr_emerg("FLEET_PROBE: int_sqrt n=%lu bad=%lu firstbad=%ld verdict=%s\n", c, bad, fb, bad==0?"DIFF_PASS":"DIFF_FAIL"); }
	{ unsigned long c=0,bad=0; long fb=-1; unsigned long a,b;(void)a;(void)b;
	  {u32 w;for(w=0;w<200000u;w++){c++;if(cgir_sw_hweight32(w)!=__sw_hweight32(w)){bad++;if(fb<0)fb=w;}}}
	  pr_emerg("FLEET_PROBE: __sw_hweight32 n=%lu bad=%lu firstbad=%ld verdict=%s\n", c, bad, fb, bad==0?"DIFF_PASS":"DIFF_FAIL"); }
	pr_emerg("FLEET_PROBE: done\n");
	return 0;
}
late_initcall(fleet_init);

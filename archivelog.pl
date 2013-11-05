#!/usr/bin/perl
# $Id$
# Copyright 2013 Matthew Wall
#
# logrollover for the weewx registration system
#
# Run this script periodically to compress the log file.

use strict;
use POSIX;

my $version = '$Id$';

my $basedir = '/home/content/t/o/m/tomkeffer';

# location of the log file
my $logfn = "$basedir/html/register/register.log";

# format for filename timestamp
my $DATE_FORMAT_FN = "%Y%m%d.%H%M%S";
my $ts = strftime $DATE_FORMAT_FN, gmtime time;

my $oldfn = $logfn;
my $newfn = "$logfn.$ts";

`mv $oldfn $newfn`;
`touch $oldfn`;
`gzip $newfn`;

exit 0;

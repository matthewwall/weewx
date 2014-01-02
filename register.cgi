#!/usr/bin/perl
# $Id$
# Copyright 2013 Matthew Wall
#
# register/update a weewx station via GET or POST request
#
# This CGI script takes requests from weewx stations and registers them into
# a database.
#
# The station_url is used to uniquely identify a station.
#
# If the station has never been seen before, a new record is added.  If the
# station has been seen, then a field is updated with the timestamp of the
# request.
#
# Data are saved to a database.  The database contains a single table.
#
# If the database does not exist, one will be created with an empty table.
#
# FIXME: should we have a field for first_seen?
# FIXME: add checks to prevent update too frequently

use strict;
use POSIX;

my $version = '$Id$';

my $basedir = '/home/content/t/o/m/tomkeffer';

# location of the station database
my $db = "$basedir/weereg/stations.sdb";

# location of the history database
my $histdb = "$basedir/weereg/history.sdb";

# location of the html generator
my $genhtmlapp = "$basedir/html/register/mkstations.pl";

# location of the log archiver
my $arclogapp = "$basedir/html/register/archivelog.pl";

# location of the count app
my $savecntapp = "$basedir/html/register/savecounts.pl";

# location of the log file
my $logfile = "$basedir/html/register/register.log";

# format of the date as returned in the html footers
my $DATE_FORMAT = "%Y.%m.%d %H:%M:%S UTC";

# parameters that we recognize
my @params = qw(station_url description latitude longitude station_type station_model weewx_info python_info platform_info);

my $RMETHOD = $ENV{'REQUEST_METHOD'};
if($RMETHOD eq 'GET' || $RMETHOD eq 'POST') {
    my($qs,%rqpairs) = &getrequest;
    if($rqpairs{action} eq 'chkenv') {
        &checkenv();
    } elsif($rqpairs{action} eq 'genhtml') {
        &runcmd('generate html', $genhtmlapp);
    } elsif($rqpairs{action} eq 'arclog') {
        &runcmd('archive log', $arclogapp);
    } elsif($rqpairs{action} eq 'getcounts') {
        &runcmd('save counts', $savecntapp);
    } elsif($rqpairs{action} eq 'history') {
        &history(%rqpairs);
    } else {
        &handleregistration(%rqpairs);
    }
} else {
    &writereply('Bad Request', 'FAIL', "Unsupported request method '$RMETHOD'.");
}

exit 0;



# figure out the environment in which we are running
sub checkenv {
    my $title = 'checkenv';
    my $tstr = &getformatteddate();
    &writecontenttype();
    &writeheader($title);
    print STDOUT "<p><strong>$title</strong></p>\n";

    # perl
    my $output = `perl -V`;
    print STDOUT "<pre>\n";
    print STDOUT "$output\n";
    print STDOUT "</pre>\n";

    # web server environment
    print STDOUT "<pre>\n";
    for my $k (keys %ENV) {
        print STDOUT "$k = $ENV{$k}\n";
    }
    print STDOUT "</pre>\n";

    # file systems
    my $df = `df -k`;
    print STDOUT "<pre>\n";
    print STDOUT "$df\n";
    print STDOUT "</pre>\n";

    # databases
    print STDOUT "<pre>\n";
    my $rval = eval "{ require DBI; }"; ## no critic (ProhibitStringyEval)
    if(!$rval) {
        print STDOUT "DBI is not installed\n";
    } else {
        my @drivers = DBI->available_drivers();
        my $dstr = "DBI drivers:";
        foreach my $d (@drivers) {
            $dstr .= " $d";
        }
        print STDOUT "$dstr\n";
    }
    print STDOUT "</pre>\n";

    &writefooter($tstr);
}

sub runcmd {
    my($title, $cmd) = @_;
    my $output = q();

    if(! -f "$cmd") {
        $output = "$cmd does not exist";
    } elsif (! -x "$cmd") {
        $output = "$cmd is not executable";
    } else {
        $output = `$cmd 2>&1`;
    }

    my $tstr = &getformatteddate();
    &writecontenttype();
    &writeheader($title);
    print STDOUT "<p><strong>$title</strong></p>\n";

    print STDOUT "<pre>\n";
    print STDOUT "$cmd\n";
    print STDOUT "</pre>\n";

    print STDOUT "<pre>\n";
    print STDOUT "$output\n";
    print STDOUT "</pre>\n";

    &writefooter($tstr);    
}

sub handleregistration {
    my(%rqpairs) = @_;

    my ($status,$msg,$rec) = registerstation(%rqpairs);
    if($status eq 'OK') {
        &writereply('Registration Complete', 'OK', $msg, $rec, $rqpairs{debug});
        &updatestations();
    } else {
        &writereply('Registration Failed', 'FAIL', $msg, $rec, $rqpairs{debug});
    }
}

# update the stations web page then update the counts database
sub updatestations() {
    `$genhtmlapp >> $logfile 2>&1`;
    `$savecntapp >> $logfile 2>&1`;
}

# if this is a new station, add an entry to the database.  if an entry already
# exists, update the last_seen timestamp.
sub registerstation {
    my(%rqpairs) = @_;

    my %rec;
    foreach my $param (@params) {
        $rec{$param} = $rqpairs{$param};
    }
    $rec{last_seen} = time;
    $rec{last_addr} = $ENV{'REMOTE_ADDR'};
    $rec{user_agent} = $ENV{HTTP_USER_AGENT};

    my @msgs;
    if($rec{station_url} =~ /example.com/) {
        push @msgs, 'example.com is not a real URL';
    }
    if($rec{station_url} =~ /weewx.com/) {
        push @msgs, 'weewx.com does not host any weather stations';
    }
    if($rec{station_url} =~ /register.cgi/) {
        push @msgs, 'station_url should be the URL to your weather station';
    }
    if($rec{station_url} !~ /^https?:\/\/\S+\.\S+/) {
        push @msgs, 'station_url is not a proper URL';
    }
    if($rec{station_url} =~ /'/) {
        push @msgs, 'station_url cannot contain single quotes';
    }
    if($rec{station_type} eq q() || $rec{station_type} !~ /\S/) {
        push @msgs, 'station_type must be specified';
    } elsif($rec{station_type} =~ /'/) {
        push @msgs, 'station_type cannot contain single quotes';
    }
    if($rec{latitude} eq q()) {
        push @msgs, 'latitude must be specified';
    } elsif($rec{latitude} =~ /[^0-9.-]+/) {
        push @msgs, 'latitude must be decimal notation, for example 54.234 or -23.5';
    } elsif($rec{latitude} < -90 || $rec{latitude} > 90) {
        push @msgs, 'latitude must be between -90 and 90, inclusive';
    }
    if($rec{longitude} eq q()) {
        push @msgs, 'longitude must be specified';
    } elsif($rec{longitude} =~ /[^0-9.-]+/) {
        push @msgs, 'longitude must be decimal notation, for example 7.15 or -78.535';
    } elsif($rec{longitude} < -180 || $rec{longitude} > 180) {
        push @msgs, 'longitude must be between -180 and 180, inclusive';
    }
    for my $k ('description','station_model','weewx_info','python_info','platform_info') {
        if($rec{$k} =~ /'/) {
            $rec{$k} =~ s/'//g;
        }
    }
    if($#msgs >= 0) {
        my $msg = q();
        foreach my $m (@msgs) {
            $msg .= '; ' if $msg ne q();
            $msg .= $m;
        }
        return (-1, $msg, \%rec);
    }

    my $rval = eval "{ require DBI; }"; ## no critic (ProhibitStringyEval)
    if(!$rval) {
        my $msg = 'bad server configuration: DBI is not installed';
        return ('FAIL', $msg, \%rec);
    }
    my $havesqlite = 0;
    my @drivers = DBI->available_drivers();
    foreach my $d (@drivers) {
        $havesqlite = 1 if $d =~ /^sqlite/i;
    }
    if(!$havesqlite) {
        my $msg = 'bad server configuration: DBI::SQLite is not installed';
        return ('FAIL', $msg, \%rec);
    }

    my $dbexists = -f $db;
    my $dbh = DBI->connect("dbi:SQLite:$db", q(), q(), { RaiseError => 0 });
    if (!$dbh) {
        my $msg = 'connection to database failed: ' . $DBI::errstr;
        return ('FAIL', $msg, \%rec);
    }

    my $rc = 0;
    if(! $dbexists) {
        $rc = $dbh->do('create table stations(station_url varchar2(255) not NULL, description varchar2(255), latitude number, longitude number, station_type varchar2(64), station_model varchar2(64), weewx_info varchar2(64), python_info varchar2(64), platform_info varchar2(64), last_addr varchar2(16), last_seen int)');
        if(!$rc) {
            my $msg = 'create table failed: ' . $DBI::errstr;
            $dbh->disconnect();
            return ('FAIL', $msg, \%rec);
        }
        $rc = $dbh->do('create unique index index_stations on stations(station_url asc, latitude asc, longitude asc, station_type asc, station_model asc, weewx_info asc, python_info asc, platform_info asc, last_addr asc)');
        if(!$rc) {
            my $msg = 'create index failed: ' . $DBI::errstr;
            $dbh->disconnect();
            return ('FAIL', $msg, \%rec);
        }
    }

    # if data are different from latest record, save a new record.  otherwise
    # just update the timestamp of the matching record.

    my $sth = $dbh->prepare(q{insert or replace into stations (station_url,description,latitude,longitude,station_type,station_model,weewx_info,python_info,platform_info,last_addr,last_seen) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)});
    if(!$sth) {
        my $msg = 'prepare failed: ' . $DBI::errstr;
        $dbh->disconnect();
        return ('FAIL', $msg, \%rec);
    }
    $rc = $sth->execute($rec{station_url},$rec{description},$rec{latitude},$rec{longitude},$rec{station_type},$rec{station_model},$rec{weewx_info},$rec{python_info},$rec{platform_info},$rec{last_addr},$rec{last_seen});
    if(!$rc) {
        my $msg = 'execute failed: ' . $DBI::errstr;
        $dbh->disconnect();
        return ('FAIL', $msg, \%rec);
    }

    $dbh->disconnect();

    return ('OK', 'registration received', \%rec);
}

sub writereply {
    my($title, $status, $msg, $rec, $debug) = @_;

    my $tstr = &getformatteddate();
    &writecontenttype();
    &writeheader($title);
    print STDOUT "<p><strong>$title</strong></p>\n";
    print STDOUT "<pre>\n";
    print STDOUT "$status: $msg\n";
    print STDOUT "</pre>\n";
    if($rec && $debug) {
        print STDOUT "<pre>\n";
        foreach my $param (@params) {
            print STDOUT "$param: $rec->{$param}\n";
        }
        print STDOUT "last_addr: $rec->{last_addr}\n";
        print STDOUT "last_seen: $rec->{last_seen}\n";
        print STDOUT "user_agent: $rec->{user_agent}\n";
        print STDOUT "\n";
        print STDOUT "HTTP_REQUEST_METHOD: $ENV{HTTP_REQUEST_METHOD}\n";
        print STDOUT "HTTP_REQUEST_URI: $ENV{HTTP_REQUEST_URI}\n";
        print STDOUT "</pre>\n";
    }
    &writefooter($tstr);
}

sub get_history_data {
    use DBI;
    my @times;
    my @counts;
    my @stypes;

    my $errmsg = q();
    my $dbh = DBI->connect("dbi:SQLite:$histdb", q(), q(), {RaiseError => 0});
    if (!$dbh) {
        $errmsg = "cannot connect to database: $DBI::errstr";
        return \@times, \@counts, \@stypes;
    }

    my $sth = $dbh->prepare("select station_type from history group by station_type");
    if (!$sth) {
        $errmsg = "cannot prepare select statement: $DBI::errstr";
        return \@times, \@counts, \@stypes;
    }
    $sth->execute();
    $sth->bind_columns(\my($st));
    while($sth->fetch()) {
        push @stypes, $st;
    }
    $sth->finish();
    undef $sth;

    $sth = $dbh->prepare("select datetime from history group by datetime order by datetime asc");
    if (!$sth) {
        $errmsg = "cannot prepare select statement: $DBI::errstr";
        return \@times, \@counts, \@stypes;
    }
    $sth->execute();
    $sth->bind_columns(\my($ts));
    while($sth->fetch()) {
        push @times, $ts;
    }
    $sth->finish();
    undef $sth;

    foreach my $t (@times) {
	my %c;
	foreach my $s (@stypes) {
	    $c{$s} = 0;
	}
        my $sth = $dbh->prepare("select station_type,active,stale from history where datetime=$t");
        if (!$sth) {
            $errmsg = "cannot prepare select statement: $DBI::errstr";
            return \@times, \@counts, \@stypes;
        }
        $sth->execute();
        $sth->bind_columns(\my($st,$active,$stale));
        while($sth->fetch()) {
	    $c{$st} = $active;
        }
        $sth->finish();
        undef $sth;
	push @counts, \%c;
    }

    $dbh->disconnect();
    undef $dbh;

    return \@times, \@counts, \@stypes;
}

sub history {
    my(%rqpairs) = @_;

    my($tref, $cref, $sref) = get_history_data();
    my @times = @$tref;
    my @counts = @$cref;
    my @stations = @$sref;

    my $stacked = $rqpairs{stacked} eq '0' ? '0' : '1';
    my $sequential = $rqpairs{sequential} eq '1' ? '1' : '0';
    my $fill = $rqpairs{fill} eq '1' ? '1' : '0';

    my $tstr = &getformatteddate();
    &writecontenttype();
    print STDOUT <<EoB1;
<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 3.2 Final//EN\">
<html>
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
  <title>history</title>
  <script>
EoB1

    print STDOUT "var data = {\n";
    print STDOUT "time: [";
    for(my $i=0; $i<scalar(@times); $i++) {
        print STDOUT "," if $i > 0;
        print STDOUT "$times[$i]";
    }
    print STDOUT "],\n";
    print STDOUT "totals: [";
    for(my $i=0; $i<scalar(@times); $i++) {
        print STDOUT "," if $i > 0;
        print STDOUT "$counts[$i]{total}";
    }
    print STDOUT "],\n";
    print STDOUT "stations: [\n";
    foreach my $k (@stations) {
        next if $k eq 'total';
        print STDOUT "{ name: '$k', ";
        print STDOUT "values: [";
        for (my $j=0; $j<scalar(@times); $j++) {
            print STDOUT "," if $j > 0;
            print STDOUT "$counts[$j]{$k}";
        }
        print STDOUT "] },\n";
    }
    print STDOUT "]\n";
    print STDOUT "}\n";

    print STDOUT <<EoB2;
function draw_plot(stacked, sequential, fill) {
  var colors = [ '#ff0000', '#880000', '#00aa00', '#005500',
                 '#0000ff', '#000088', '#000000', '#888800',
                 '#00aaaa', '#008888', '#ff00ff', '#880088' ];
  var fills =  [ '#ffaaaa', '#885555', '#00aa00', '#005500',
                 '#0000ff', '#000088', '#dddddd', '#888800',
                 '#00aaaa', '#008888', '#ff00ff', '#880088' ];
  var canvas = document.getElementById('history_canvas');
  canvas.width = 1000;
  canvas.height = 800;
  var c = canvas.getContext('2d');
  c.font = '10px sans-serif';
  var rbuf = 80;
  var rpad = 5;
  var vpad = 15;
  var w = c.canvas.width;
  var plotw = w - rbuf;
  var h = c.canvas.height;
  var maxcnt = 0;
  for(var i=0; i<data.totals.length; i++) {
    if(data.totals[i] > maxcnt) {
      maxcnt = data.totals[i];
    }
  }
  var timemin = 9999999999999;
  var timemax = 0;
  for(var i=0; i<data.time.length; i++) {
    if(data.time[i] < timemin) {
      timemin = data.time[i];
    }
    if(data.time[i] > timemax) {
      timemax = data.time[i];
    }
  }
  var y = Math.round(h / maxcnt);
  var x = Math.round(plotw / data.time.length);
  var sorted = data.stations.reverse(sorter);
  var sums = Array(data.time.length);
  for(var i=0; i<sums.length; i++) { sums[i] = 0; }
  for(var i=0; i<sorted.length; i++) {
    for(var j=0; j<data.time.length; j++) {
      sums[j] += sorted[i].values[j];
    }
  }

  var used = Array();
  for(var i=0; i<sorted.length; i++) {
    c.fillStyle = fills[i%colors.length];
    c.strokeStyle = colors[i%colors.length];
    c.beginPath();
    c.moveTo(0,h);
    var xval = 0;
    var yval = 0;
    for(var j=0; j<data.time.length; j++) {
      if(sequential) {
        xval = j*x;
      } else {
        xval = plotw * (data.time[j] - timemin) / (timemax - timemin);
      }
      if(stacked) {
        yval = h-sums[j]*y;
      } else {
        yval = h-sorted[i].values[j]*y;
      }
      c.lineTo(xval, yval);
      sums[j] -= sorted[i].values[j];
    }
    if(fill) {
      c.lineTo(xval,h);
      c.fill();
    } else {
      c.stroke();
    }
    while(used[yval]) {
      yval += vpad;
    }
    c.fillStyle = colors[i%colors.length];
    c.fillText(sorted[i].name, plotw+rpad, yval);
    used[yval] = 1;
  }

  /* horizontal and vertial axes */
  c.fillStyle = "#000000";
  c.strokeStyle = "#000000";
  c.beginPath();
  c.moveTo(1, 1);
  c.lineTo(1, h-1);
  c.lineTo(w, h-1);
  c.stroke();
  /* tick marks on the axes */
  var ticwidth = 4;
  for(var j=0; j*y<h; j++) {
    c.beginPath();
    c.moveTo(w-1, j*y);
    c.lineTo(w-ticwidth, j*y);
    if(j%5 == 0) {
      c.lineTo(w-ticwidth*2, j*y);
      c.fillText(j, w-ticwidth*2-rpad*3, h-j*y);
    }
    c.stroke();
  }
}

function sorter(a,b) {
  if(a.name < b.name)
    return -1;
  if(a.name > b.name)
    return 1;
  return 0;
}
  </script>
</head>
<body onload='draw_plot($stacked,$sequential,$fill);'>
<canvas id='history_canvas'></canvas>
<br/>
EoB2

    &writefooter($tstr);
}

sub writecontenttype {
    my($type) = @_;

    $type = "text/html" if $type eq "";
    print STDOUT "Content-type: text/html\n\n";
}

sub writeheader {
    my($title,$head) = @_;

    print STDOUT "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 3.2 Final//EN\">\n";
    print STDOUT "<html>\n";
    print STDOUT "<head>\n";
    print STDOUT "  <title>$title</title>\n";
    print STDOUT "$head\n";
    print STDOUT "</head>\n";
    print STDOUT "<body>\n";
};

sub writefooter {
    my($mdate) = @_;

    if($mdate) {
        print STDOUT "<small><i>\n";
        print STDOUT "$mdate<br/>\n";
        print STDOUT "$version<br/>\n";
        print STDOUT "</i></small>\n";
    }

    print STDOUT "\n</body>\n</html>\n";
}

sub getformatteddate {
    return strftime $DATE_FORMAT, gmtime time;
}

sub getrequest {
    my $request = q();
    if ($ENV{'REQUEST_METHOD'} eq "POST") {
        read(STDIN, $request, $ENV{'CONTENT_LENGTH'});
    } elsif ($ENV{'REQUEST_METHOD'} eq "GET" ) {
        $request = $ENV{'QUERY_STRING'};
    }
    my $delim = ',';
    my %pairs;
    foreach my $pair (split(/[&]/, $request)) {
        $pair =~ tr/+/ /;
        $pair =~ s/%(..)/pack("c",hex($1))/ge;
        my($loc) = index($pair,"=");
        my($name) = substr($pair,0,$loc);
        my($value) = substr($pair,$loc+1);
        if($pairs{$name} eq "") {
            $pairs{$name} = $value;
        } else {
            $pairs{$name} .= "${delim}$value";
        }
    }
    return($ENV{'QUERY_STRING'},%pairs);
}

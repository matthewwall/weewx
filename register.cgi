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

# location of the sqlite database
my $db = "$basedir/weereg/stations.sdb";

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
    my $tstr = &getformatteddate;
    &writecontenttype;
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

    my $tstr = &getformatteddate;
    &writecontenttype;
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

# update the stations web page then update the counts database
sub updatestations() {
    `$genhtmlapp >> $logfile 2>&1`;
    `$savecntapp >> $logfile 2>&1`;
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

    my $tstr = &getformatteddate;
    &writecontenttype;
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

sub writecontenttype {
    my($type) = @_;

    $type = "text/html" if $type eq "";
    print STDOUT "Content-type: text/html\n\n";
}

sub writeheader {
    my($title) = @_;

    print STDOUT "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 3.2 Final//EN\">\n";
    print STDOUT "<html>\n";
    print STDOUT "<head>\n";
    print STDOUT "  <title>$title</title>\n";
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

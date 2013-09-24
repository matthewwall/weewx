#!/usr/bin/perl
# $Id$
# register/update a weewx station via GET or POST request
# Copyright 2013 Matthew Wall
#
# This CGI script takes requests from weewx stations and registers them into
# a database.  It expects the following parameters:
#
# station_url
# description
# latitude
# longitude
# station_type
# weewx_info
# python_info
# platform_info
#
# The station_url is used to uniquely identify a station.
#
# If the station has never been seen before, a new record is added.  If the
# station has been seen, then a field is updated with the timestamp of the
# request.
#
# Data are saved to a sqlite database.  The database contains a single table
# with the following structure:
#
# create table stations (station_url varchar2(255) primary key,
#                        description varchar2(255),
#                        latitude number,
#                        longitude number,
#                        station_type varchar2(64),
#                        weewx_info varchar2(64),
#                        python_info varchar2(64),
#                        platform_info varchar2(64),
#                        last_addr varchar2(16),
#                        last_seen int)
#
# If the database does not exist, one will be created with an empty table.
#
# FIXME: should we have a field for first_seen?
# FIXME: add checks to prevent update too frequently

use strict;
use POSIX;

my $basedir = '/home/content/t/o/m/tomkeffer';

# location of the sqlite database
my $db = "$basedir/weereg/stations.sdb";

# location of the html generator and template
my $htmlapp = "$basedir/html/register/mkstations.pl";

# format of the date as returned in the html footers
my $DATE_FORMAT = "%Y.%m.%d %H:%M:%S UTC";

my $RMETHOD = $ENV{'REQUEST_METHOD'};
if($RMETHOD eq 'GET' || $RMETHOD eq 'POST') {
    my($qs,%rqpairs) = &getrequest;
    if($rqpairs{action} eq 'chkenv') {
        &checkenv();
    } elsif($rqpairs{action} eq 'genhtml') {
        &genhtml();
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

# generate the html page from template
sub genhtml {
    my $cmd = "$htmlapp";
    my $output = q();

    if(! -f "$htmlapp") {
        $output = "$htmlapp does not exist";
    } elsif (! -x "$htmlapp") {
        $output = "$htmlapp is not executable";
    } else {
        $output = `$cmd 2>&1`;
    }

    my $title = 'genhtml';
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

sub handleregistration {
    my(%rqpairs) = @_;

    my ($status,$msg,$rec) = registerstation(%rqpairs);
    if($status eq 'OK') {
        &writereply('Registration Complete', 'OK', $msg, $rec);
    } else {
        &writereply('Registration Failed', 'FAIL', $msg, $rec);
    }
}

# if this is a new station, add an entry to the database.  if an entry already
# exists, update the last_seen timestamp.
sub registerstation {
    my(%rqpairs) = @_;

    my %rec;
    $rec{station_url} = $rqpairs{station_url};
    $rec{description} = $rqpairs{description};
    $rec{latitude} = $rqpairs{latitude};
    $rec{longitude} = $rqpairs{longitude};
    $rec{station_type} = $rqpairs{station_type};
    $rec{weewx_info} = $rqpairs{weewx_info};
    $rec{python_info} = $rqpairs{python_info};
    $rec{platform_info} = $rqpairs{platform_info};
    my $addr = $ENV{'REMOTE_ADDR'};
    $rec{last_addr} = $addr;
    my $ts = time;
    $rec{last_seen} = $ts;

    my @msgs;
    if($rec{station_url} =~ /example.com/) {
        push @msgs, 'example.com is not a real URL';
    }
    if($rec{station_url} !~ /^https?:\/\/\S+\.\S+/) {
        push @msgs, 'station_url is not a proper URL';
    }
    if($rec{station_url} =~ /'/) {
        push @msgs, 'station_url cannot contain single quotes';
    }
    if($rec{description} =~ /'/) {
        push @msgs, 'description cannot contain single quotes';
    }
    if($rec{station_type} =~ /'/) {
        push @msgs, 'station_type cannot contain single quotes';
    }
    if($rec{latitude} eq q()) {
        push @msgs, 'latitude must be specified';
    } elsif($rec{latitude} =~ /[^0-9.-]+/) {
        push @msgs, 'latitude must be decimal notation, for example 54.234 or -23.5';
    }
    if($rec{longitude} eq q()) {
        push @msgs, 'longitude must be specified';
    } elsif($rec{longitude} =~ /[^0-9.-]+/) {
        push @msgs, 'longitude must be decimal notation, for example 7.15 or -78.535';
    }
    for my $k ('weewx_info','python_info','platform_info') {
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
        $rc = $dbh->do('create table stations(station_url varchar2(255) not NULL, description varchar2(255), latitude number, longitude number, station_type varchar2(64), weewx_info varchar2(64), python_info varchar2(64), platform_info varchar2(64), last_addr varchar2(16), last_seen int)');
        if(!$rc) {
            my $msg = 'create table failed: ' . $DBI::errstr;
            $dbh->disconnect();
            return ('FAIL', $msg, \%rec);
        }
        $rc = $dbh->do('create unique index index_stations on stations(station_url asc, latitude asc, longitude asc, station_type asc, weewx_info asc, python_info asc, platform_info asc, last_addr asc)');
        if(!$rc) {
            my $msg = 'create index failed: ' . $DBI::errstr;
            $dbh->disconnect();
            return ('FAIL', $msg, \%rec);
        }
    }

    # if data are different from latest record, save a new record.  otherwise
    # just update the timestamp of the latest record.

    my $qs = "insert or replace into stations (station_url,description,latitude,longitude,station_type,weewx_info,python_info,platform_info,last_addr,last_seen) values ('$rec{station_url}','$rec{description}','$rec{latitude}','$rec{longitude}','$rec{station_type}','$rec{weewx_info}','$rec{python_info}','$rec{platform_info}','$addr',$ts)";
    $rc = $dbh->do($qs);
    if(!$rc) {
        my $msg = 'insert/replace failed: ' . $DBI::errstr;
        $dbh->disconnect();
        return ('FAIL', $msg, \%rec);
    }

    $dbh->disconnect();

    return ('OK', 'registration received', \%rec);
}

sub writereply {
    my($title, $status, $msg, $rec) = @_;

    my $tstr = &getformatteddate;
    &writecontenttype;
    &writeheader($title);
    print STDOUT "<p><strong>$title</strong></p>\n";
    print STDOUT "<pre>\n";
    print STDOUT "$status: $msg\n";
    print STDOUT "</pre>\n";
    print STDOUT "<pre>\n";
    print STDOUT "station_url: $rec->{station_url}\n";
    print STDOUT "description: $rec->{description}\n";
    print STDOUT "latitude: $rec->{latitude}\n";
    print STDOUT "longitude: $rec->{longitude}\n";
    print STDOUT "station_type: $rec->{station_type}\n";
    print STDOUT "weewx_info: $rec->{weewx_info}\n";
    print STDOUT "python_info: $rec->{python_info}\n";
    print STDOUT "platform_info: $rec->{platform_info}\n";
    print STDOUT "last_addr: $rec->{last_addr}\n";
    print STDOUT "last_seen: $rec->{last_seen}\n";
    print STDOUT "</pre>\n";
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
        print STDOUT "$mdate\n";
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

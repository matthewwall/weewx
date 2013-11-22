#!/usr/bin/perl
# $Id$
# Copyright 2013 Matthew Wall
#
# insert fields from database into template html file, resulting in a web page
# with a map and list of stations.
#
# Run this script periodically to update the web page.

use strict;
use DBI;
use POSIX;

my $version = '$Id$';

my $basedir = '/home/content/t/o/m/tomkeffer';

# location of the sqlite database
my $db = "$basedir/weereg/stations.sdb";

# html template file
my $tmpl = "$basedir/html/register/stations.html.in";

# where to put the results
my $ofile = "$basedir/html/stations.html";

# how long ago do we consider stale, in seconds
my $stale = 2_592_000; # 30 days

# format for logging
my $DATE_FORMAT_LOG = "%b %d %H:%M:%S";

# format for web page display
my $DATE_FORMAT_HTML = "%H:%M:%S %d %b %Y UTC";

while($ARGV[0]) {
    my $arg = shift;
    if ($arg eq '--template') {
        $tmpl = shift;
    } elsif ($arg eq '--stale') {
        $stale = shift;
    } elsif ($arg eq '--db') {
        $db = shift;
    } elsif ($arg eq '--ofile') {
        $ofile = shift;
    }
}



# read the template file, cache in memory
my $contents = q();
if(open(IFILE, "<$tmpl")) {
    while(<IFILE>) {
        $contents .= $_;
    }
    close(IFILE);
} else {
    my $errmsg = "cannot read template file $tmpl: $!";
    errorpage($errmsg);
    logerr($errmsg);
    exit 1;
}

# query for the latest record for each station (identified by url)
my @records;
my $errmsg = q();
# be sure the database is there
if (-f $db) {
    # read the database, keep only records that are not stale
    my $dbh = DBI->connect("dbi:SQLite:$db", q(), q(), { RaiseError => 0 });
    if ($dbh) {
        my $now = time;
        my $cutoff = $now - $stale;
# FIXME: these queries do the right thing in sqlite3, but not in perl DBI
#	my $qry = "select station_url,description,latitude,longitude,station_type,last_seen from (select * from stations where last_seen > $cutoff order by last_seen asc) group by station_url";
#	my $qry = "select station_url,description,latitude,longitude,station_type,last_seen from (select * from stations order by last_seen asc) t1 where t1.last_seen > $cutoff group by t1.station_url";
        # since doing it in the db query does not work, query for everything
        # then do the filtering in perl.  sigh.
        my $qry = "select station_url,description,latitude,longitude,station_type,last_seen from stations where last_seen > $cutoff order by last_seen";
        my $sth = $dbh->prepare($qry);
	if ($sth) {
            my %unique;
	    $sth->execute();
	    $sth->bind_columns(\my($url,$desc,$lat,$lon,$st,$ts));
	    while($sth->fetch()) {
		my %r;
		$r{url} = $url;
		$r{description} = $desc;
		$r{latitude} = $lat;
		$r{longitude} = $lon;
		$r{station_type} = $st;
		$r{last_seen} = $ts;
                if(!defined($unique{$url}) || $ts>$unique{$url}->{last_seen}) {
                    $unique{$url} = \%r;
                }
            }
            $sth->finish();
            undef $sth;
            foreach my $k (keys %unique) {
		push @records, $unique{$k};
            }
	} else {
	    $errmsg = "cannot prepare select statement: $DBI::errstr";
	    logerr($errmsg);
	}
	$dbh->disconnect();
        undef $dbh;
    } else {
	$errmsg = "cannot connect to database: $DBI::errstr";
	logerr($errmsg);
    }
} else {
    $errmsg = "no database at $db";
    logerr($errmsg);
}

# inject into the template and spit it out
if(open(OFILE,">$ofile")) {
    foreach my $line (split("\n", $contents)) {
        if($line =~ /^var sites = /) {
            if ($errmsg ne q()) {
                print OFILE "/* error: $errmsg */\n";
            }
            print OFILE "var sites = [\n";
            foreach my $rec (sort { trim($a->{description}) cmp trim($b->{description}) } @records) {
                print OFILE "  { description: '$rec->{description}',\n";
                print OFILE "    url: '$rec->{url}',\n";
                print OFILE "    latitude: $rec->{latitude},\n";
                print OFILE "    longitude: $rec->{longitude},\n";
                print OFILE "    station: '$rec->{station_type}',\n";
                print OFILE "    last_seen: $rec->{last_seen} },\n";
                print OFILE "\n";
            }
            print OFILE "];\n";
        } elsif($line =~ /LAST_MODIFIED/) {
            my $tstr = strftime $DATE_FORMAT_HTML, gmtime time;
            my $n = $stale / 86_400;
            print OFILE "stations will be removed after $n days without contact<br/>\n";
            print OFILE "this station listing is updated every 10 minutes<br/>\n";
            print OFILE "last update $tstr<br/>\n";
            print OFILE "<!-- $version -->\n";
        } else {
            print OFILE "$line\n";
        }
    }
    close(OFILE);
    my $cnt = scalar @records;
    logout("processed $cnt stations");
} else {
    logerr("cannot write to output file $ofile: $!");
}

exit 0;


sub errorpage {
    my ($msg) = @_;
    if(open(OFILE,">$ofile")) {
        print OFILE "<html>\n";
        print OFILE "<head>\n";
        print OFILE "  <title>error</title>\n";
        print OFILE "</head>\n";    
        print OFILE "<body>\n";
        print OFILE "<p>Creation of stations page failed.</p>\n";
        print OFILE "<p>\n";
        print OFILE "$msg\n";
        print OFILE "</p>\n";
        print OFILE "</body>\n";
        print OFILE "</html>\n";
        close(OFILE);
    } else {
        logerr("cannot write to output file $ofile: $!");
    }
}

sub logout {
    my ($msg) = @_;
    my $tstr = strftime $DATE_FORMAT_LOG, gmtime time;
    print STDOUT "$tstr $msg\n";
}

sub logerr {
    my ($msg) = @_;
    my $tstr = strftime $DATE_FORMAT_LOG, gmtime time;
    print STDERR "$tstr $msg\n";
}

# strip any leading whitespace or non-alphanumeric characters from beginning,
# then return lowercase.
sub trim {
    (my $s = $_[0]) =~ s/^\s+|^[^A-Za-z0-9]+//g;
    return "\L$s";
}

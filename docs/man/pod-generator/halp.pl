#!/usr/bin/env perl

use warnings;
use strict;
use 5.010;

# tool to make a pod file generated from equo --help
# which can be used to make a manual page

# A tip:
# if this tool gives badly formatted text due to missing \t etc. in equo
# output, it's easier to save equo output to file, correct it and pipe
# using cat.

# by Enlik

my @strs;
# @strs: { <el> }, { <el> }, ...
# el - hash: indent => <0..n>, command => <str>, desc => <str>

# command line arg:
# -p	generate pod file

# pass to stdin equo --help (English),
# you may use: LANG=en_US.UTF-8 equo | perl <this script>

if (@ARGV and $ARGV[0] eq "-p") {
	require Pod;
	@strs = parse_input();
	
	my $conv = Pod->new(\@strs);
	$conv->generate;
}
else {
	print "bad command, use -p\n";
	exit 1;
}

sub parse_input {
	my @strs = ();
	my $level;
	my ($cmd, $desc);
	
	while (my $line = <STDIN>) {
		chomp $line;
		
		next unless $line; # omit empty
		
		# 0 level
		if ($line =~ /^  \S/) {		
			$line = substr $line, 2;
			$level = 0;
		}
		elsif ($line =~ /^\t/) {
			$level = 0;
			while ($line =~ /^\t/) {
				$level++;
				$line = substr $line, 1;
			}
		}
		else {
			if ($line and not $line =~ /~ equo ~/) {
				die "badly formatted line: $line\n";
			}
			next;
		}

		if ($line =~ /^([^\t]+)\t+(.*)/) {
			$cmd = $1;
			$desc = $2;
		}
		else {
			$cmd = "";
			$desc = $line;
		}
		## hop <branch>	upgrade your distribution to a new release (branch)
		#if ($line =~ /^(\S+ <.+>)\s*(.*)/) {
			#$cmd = $1;
			#$desc = $2;
		#}
		## notice [repos]	repository notice board reader
		#elsif ($line =~ /^(\S+ \[.+\])\s*(.*)/) {
			#$cmd = $1;
			#$desc = $2;
		#}
		## search		search packages in repositories
		#elsif ($line =~ /^(\S+)\s*(.*)/) {
			#$cmd = $1;
			#$desc = $2;
		#}
		#else {
			## ?
			#$level = 0;
			#$cmd = "";
			#$desc = $line;
		#}
		
		push @strs, { indent => $level, command => $cmd, desc => $desc };
	}
	@strs;
}
# Copyright 2012 Jose Blanca, Peio Ziarsolo, COMAV-Univ. Politecnica Valencia
# This file is part of seq_crumbs.
# seq_crumbs is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

# seq_crumbs is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR  PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with seq_crumbs. If not, see <http://www.gnu.org/licenses/>.

# pylint: disable=C0111

from __future__ import division

try:
    import pysam
except ImportError:
    # This is an optional requirement
    pass

from crumbs.utils.tags import SEQS_PASSED, SEQS_FILTERED_OUT
from crumbs.utils.seq_utils import uppercase_length, get_uppercase_segments
from crumbs.exceptions import WrongFormatError
from crumbs.blast import Blaster
from crumbs.statistics import calculate_dust_score
from crumbs.settings import get_setting
from crumbs.utils.sam import IS_UNMAPPED, bit_tag_is_in_int_flag


def seq_to_filterpackets(seq_packets):
    'It yields packets suitable for the filters'
    for packet in seq_packets:
        yield {SEQS_PASSED: packet, SEQS_FILTERED_OUT: []}


class FilterByFeatureTypes(object):
    'It filters out sequences not annotated with the given feature types'
    def __init__(self, feature_types, reverse=False):
        '''The initiator

        feat_types is a list of types of features to use to filter'''

        self._feat_types = feature_types
        self._reverse = reverse

    def __call__(self, filterpacket):
        'It filters out sequences not annotated with the given feature types'
        reverse = self._reverse
        feat_types = self._feat_types
        seqs_passed = []
        filtered_out = filterpacket[SEQS_FILTERED_OUT][:]

        for seqrec in filterpacket[SEQS_PASSED]:
            feats_in_seq = [f.type for f in seqrec.features if f.type in feat_types]
            passed = True if feats_in_seq else False
            if reverse:
                passed = not(passed)
            if passed:
                seqs_passed.append(seqrec)
            else:
                filtered_out.append(seqrec)

        return {SEQS_PASSED: seqs_passed, SEQS_FILTERED_OUT: filtered_out}


class FilterByRpkm(object):
    def __init__(self, read_counts, min_rpkm, reverse=False):
        '''The init

        read_counts its a dict:
            - keys are the sequence names
            - values should be dicts with length, mapped_reads and
            unmmapped_reads, counts
        '''
        self._read_counts = read_counts
        self._min_rpkm = min_rpkm
        self._total_reads = sum([v['mapped_reads'] + v['unmapped_reads'] for v in read_counts.values()])
        self._reverse = reverse

    def __call__(self, filterpacket):
        read_counts = self._read_counts
        min_rpkm = self._min_rpkm
        total_reads = self._total_reads
        reverse = self._reverse

        seqs_passed = []
        filtered_out = filterpacket[SEQS_FILTERED_OUT][:]
        for seqrecord in filterpacket[SEQS_PASSED]:
            count = read_counts[seqrecord.id]
            kb_len = count['length'] / 1000

            million_reads = total_reads / 1e6
            rpk = count['mapped_reads'] / kb_len  # rpks
            rpkm = rpk / million_reads  # rpkms
            passed = True if rpkm >= min_rpkm else False
            if reverse:
                passed = not(passed)

            if passed:
                seqs_passed.append(seqrecord)
            else:
                filtered_out.append(seqrecord)

        return {SEQS_PASSED: seqs_passed, SEQS_FILTERED_OUT: filtered_out}


class FilterByLength(object):
    'It removes the sequences according to their length.'
    def __init__(self, minimum=None, maximum=None, ignore_masked=False):
        '''The initiator.

        threshold - minimum length to pass the filter (integer)
        reverse - if True keep the short sequences and discard the long ones
        ignore_masked - If True only uppercase letters will be counted.
        '''
        if min is None and max is None:
            raise ValueError('min or max threshold must be given')
        self.min = minimum
        self.max = maximum
        self.ignore_masked = ignore_masked

    def __call__(self, filterpacket):
        'It filters out the short seqrecords.'
        min_ = self.min
        max_ = self.max
        ignore_masked = self.ignore_masked
        seqs_passed = []
        filtered_out = filterpacket[SEQS_FILTERED_OUT][:]
        for seqrecord in filterpacket[SEQS_PASSED]:
            seq = str(seqrecord.seq)
            length = uppercase_length(seq) if ignore_masked else len(seq)
            passed = True
            if min_ is not None and length < min_:
                passed = False
            if max_ is not None and length > max_:
                passed = False

            if passed:
                seqs_passed.append(seqrecord)
            else:
                filtered_out.append(seqrecord)
        return {SEQS_PASSED: seqs_passed, SEQS_FILTERED_OUT: filtered_out}


class FilterById(object):
    'It removes the sequences not found in the given set'
    def __init__(self, seq_ids, reverse=False):
        '''The initiator.

        seq_ids - An iterator with the sequence ids to keep
        reverse - if True keep the sequences not found on the list
        '''
        if not isinstance(seq_ids, set):
            seq_ids = set(seq_ids)
        self.seq_ids = seq_ids
        self.reverse = reverse

    def __call__(self, filterpacket):
        'It filters out the seqrecords not found in the list.'
        seq_ids = self.seq_ids
        reverse = self.reverse
        seqs_passed = []
        filtered_out = filterpacket[SEQS_FILTERED_OUT][:]
        for seqrecord in filterpacket[SEQS_PASSED]:
            passed = True if seqrecord.id in seq_ids else False
            if reverse:
                passed = not(passed)

            if passed:
                seqs_passed.append(seqrecord)
            else:
                filtered_out.append(seqrecord)

        return {SEQS_PASSED: seqs_passed, SEQS_FILTERED_OUT: filtered_out}


class FilterByBam(FilterById):
    'It filters the reads not mapped in the given BAM files'
    def __init__(self, bam_fpaths, min_mapq=0, reverse=False):
        seq_ids = self._get_mapped_reads(bam_fpaths, min_mapq)
        super(FilterByBam, self).__init__(seq_ids, reverse=reverse)

    def _get_mapped_reads(self, bam_fpaths, min_mapq):
        mapped_reads = []
        for fpath in bam_fpaths:
            bam = pysam.Samfile(fpath)
            mapped_reads.extend(read.qname for read in bam.fetch() if not read.is_unmapped and (not min_mapq or read.mapq > min_mapq))
        return mapped_reads


class FilterByQuality(object):
    'It removes the sequences according to its quality'
    def __init__(self, threshold, reverse=False, ignore_masked=False):
        '''The initiator.

        threshold - minimum quality to pass the filter (float)
        reverse - if True keep the sequences not found on the list
        '''
        self.threshold = float(threshold)
        self.reverse = reverse
        self.ignore_masked = ignore_masked

    def __call__(self, filterpacket):
        'It filters out the seqrecords not found in the list.'
        threshold = self.threshold
        reverse = self.reverse
        ignore_masked = self.ignore_masked

        seqs_passed = []
        filtered_out = filterpacket[SEQS_FILTERED_OUT][:]
        for seqrecord in filterpacket[SEQS_PASSED]:
            try:
                quals = seqrecord.letter_annotations['phred_quality']
            except KeyError:
                msg = 'Some of the input sequences do not have qualities: {}'
                msg = msg.format(seqrecord.id)
                raise WrongFormatError(msg)
            if ignore_masked:
                seq = str(seqrecord.seq)
                seg_quals = [quals[segment[0]: segment[1] + 1]
                                    for segment in get_uppercase_segments(seq)]
                qual = sum(sum(q) * len(q) for q in seg_quals) / len(quals)
            else:
                qual = sum(quals) / len(quals)
            passed = True if qual >= threshold else False
            if reverse:
                passed = not(passed)
            if passed:
                seqs_passed.append(seqrecord)
            else:
                filtered_out.append(seqrecord)

        return {SEQS_PASSED: seqs_passed, SEQS_FILTERED_OUT: filtered_out}


class FilterBlastMatch(object):
    'It filters a seq if there is a match against a blastdb'
    def __init__(self, database, program, filters, dbtype=None, reverse=False):
        '''The initiator
            database: path to a file with seqs or a blast database
            filter_params:
                expect_threshold
                similarty treshlod
                min_length_percentaje
        '''
        self._blast_db = database
        self._blast_program = program
        self._filters = filters
        self._reverse = reverse
        self._dbtype = dbtype

    def __call__(self, filterpacket):
        'It filters the seq by blast match'
        seqs_passed = []
        filtered_out = filterpacket[SEQS_FILTERED_OUT][:]
        seqrecords = filterpacket[SEQS_PASSED]
        matcher = Blaster(seqrecords, self._blast_db, dbtype=self._dbtype,
                          program=self._blast_program, filters=self._filters)

        for seqrecord in seqrecords:
            segments = matcher.get_matched_segments(seqrecord.id)
            if segments is None:
                passed = True
            else:
                passed = False

            if self._reverse:
                passed = not(passed)

            if passed:
                seqs_passed.append(seqrecord)
            else:
                filtered_out.append(seqrecord)

        return {SEQS_PASSED: seqs_passed, SEQS_FILTERED_OUT: filtered_out}


class FilterDustComplexity(object):
    'It filters a sequence according to its dust score'
    def __init__(self, threshold=get_setting('DEFATULT_DUST_THRESHOLD'),
                 reverse=False):
        '''The initiator
        '''
        self._threshold = threshold
        self._reverse = reverse

    def __call__(self, filterpacket):
        'It filters the seq by blast match'
        seqs_passed = []
        filtered_out = filterpacket[SEQS_FILTERED_OUT][:]

        threshold = self._threshold
        reverse = self._reverse
        for seqrecord in filterpacket[SEQS_PASSED]:
            dustscore = calculate_dust_score(seqrecord)
            passed = True if dustscore < threshold else False
            if reverse:
                passed = not(passed)
            if passed:
                seqs_passed.append(seqrecord)
            else:
                filtered_out.append(seqrecord)

        return {SEQS_PASSED: seqs_passed, SEQS_FILTERED_OUT: filtered_out}

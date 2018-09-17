#!/usr/bin/env python
import sys
import os
import sqlite3
import matplotlib
from numpy import arange, linspace, sqrt, random
import numpy as np
import argparse
from collections import defaultdict, namedtuple, Counter
import webbrowser
import re
import pysam

matplotlib.use('Agg')
from matplotlib.path import Path
import matplotlib.patches as patches
import matplotlib.pyplot as plt


def parse():
   
    parser = argparse.ArgumentParser(description='Plot transcript')
    parser.add_argument("-bsj", nargs='*', help="Path to backsplice junction bed-formatted files (listed in the same order as the input bam files)", metavar='')
    parser.add_argument("-stranded", metavar='', help="If strand-specific sequencing, indicate 'forward' if upstream reads are forward strand, otherwise indicate 'reverse' (True-Seq is 'reverse').")
    parser.add_argument("-sj", nargs='*', help="Path to canonical splice junction bed-formatted files (listed in the same order as the input bam files)", metavar='')
    parser.add_argument("-is", "--intron-scale", type=float, help="The factor by which intron white space should be reduced", metavar='')
    parser.add_argument("-b", "--bam", nargs='*', required=True, type=str, help="Path to each bam file", metavar='')
    parser.add_argument("-c", "--color", default="#C21807", type=str, help="Exon color. Hex colors (i.e. \"\#4286f4\". For hex, an escape \"\\\" must precede the argument), RGB (i.e. 211,19,23) or names (i.e. \"red\")", metavar='')
    parser.add_argument("-t", "--transcript",  type=str, help='Name of transcript to plot', metavar='')
    parser.add_argument("-g", "--gene", type=str, help='Name of gene to plot (overrides "-t" flag). Will plot the longest transcript derived from that gene', metavar='')
    parser.add_argument("-f", "--filter", default=0, type=int, metavar='', help='Filter out sj and circles that have fewer than this number of counts.')
    parser.add_argument("-n", "--normalize", action='store_true', help='Normalize coverage between samples')
    parser.add_argument("-rc", "--reduce_canonical", type=float, help='Factor by which to reduce canonical curves', metavar='')
    parser.add_argument("-rbs", "--reduce_backsplice", type=float, help='Factor by which to reduce backsplice curves', metavar='')
    parser.add_argument("-ro", "--repress_open", action='store_true', help='Do not open plot in browser (only save it)')
    parser.add_argument("-en", "--exon_numbering", action='store_true', help='Label exons')
    parser.add_argument("-gtf", required=True, help="Path to gtf file", metavar='')
    args = parser.parse_args()

    # Check GTF, BAM, SJ, and BSJ paths.
    if not os.path.exists(args.gtf):
        sys.exit("GTF: {} was not found".format(args.gtf))

    for path in args.bam: 
        if not os.path.exists(path):
            sys.exit("BAM: {} was not found".format(path))

    if args.sj:
        for path in args.sj: 
            if not os.path.exists(path):
                sys.exit("Splice junction file {} was not found.\n If you want to obtain splice junction reads from the input bam files, don't specify a splice junction file.".format(path))
    
    if args.bsj:
        for path in args.bsj: 
            if not os.path.exists(path):
                sys.exit("Backsplice junction file {} was not found.\n If you want to obtain backsplice junction reads from the input bam files, don't specify a backsplice junction file".format(path))

    if not (args.gene or args.transcript): 
        sys.exit('Either a gene or a transcript must be specified. (ex. "-t ENST00000390665" or "-g EGFR")')    

    #  Color: if not in rgb format already (i.e. 123,11,0)
    if not len(args.color) == 3:
        args.color = to_rgb(args.color)
    else:
        try:
            color = list(map(int, args.color.split(',')))
            args.color = [c / 255 for c in color]  # Matplotlib requires rgb values to be between 0 and 1 (rather than 0-255)
        except:
            args.color = to_rgb(args.color)
    
    return args


def calc_bez_max(p0, p1, p2, p3=None, t=0.5, quadratic=False):
  
    if quadratic:
        x = (1 - t) * (1 - t) * p0.x + 2 * (1 - t) * t * p1.x + t * t * p2.x
        y = (1 - t) * (1 - t) * p0.y + 2 * (1 - t) * t * p1.y + t * t * p2.y   

    # Cubic
    else:
        x = (1-t)*(1-t)*(1-t)*p0.x + 3*(1-t)*(1-t)*t*p1.x + 3*(1-t)*t*t*p2.x + t*t*t*p3.x
        y = (1-t)*(1-t)*(1-t)*p0.y + 3*(1-t)*(1-t)*t*p1.y + 3*(1-t)*t*t*p2.y + t*t*t*p3.y  

    return x, y


# To adjust text if collusion occurs.
class Box:

    def __init__(self, x0, x1, y0, y1):
        self.x0 = x0
        self.x1 = x1
        self.y0 = y0
        self.y1 = y1


def intersect(boxa, boxb, subtract):

    xextra = (boxa.x1 - boxa.x0)/4
    yextra = (boxa.y1 - boxa.y0)/4
    if boxa.x0 -xextra<= boxb.x0 <= boxa.x1+xextra or boxa.x0-xextra <= boxb.x1 <= boxa.x1+xextra:
        
        if boxa.y0-yextra <= boxb.y0 <= boxa.y1+yextra or boxa.y0-yextra <=boxb.y1<=boxa.y1+yextra:
            if not subtract:
                boxb.y0 += .02
                boxb.y1 += .02

            else:
                boxb.y0 -= .02
                boxb.y1 -= .02

            if random.randint(2) == 1:
                boxb.x0 += xextra/10
                boxb.x1 += xextra/10
            else:
                boxb.x0 -= xextra/10
                boxb.x1 -= xextra/10

            return intersect(boxa, boxb, subtract)
    
    ax = plt.gca()
    ymax = ax.get_ylim()[1]
    plt.ylim([plt.ylim()[0], max([ymax, boxb.y1])])

    return boxb


def draw_backsplice(ax, start, stop, y, adjust, bezier_offset, gene_size, plot=True):
    ''' Takes a start and a stop coordinate and generates a bezier curve underneath exons (for circle junctions).
        "bezier_offset" controls the depth of the circle junctions on the plot.'''


    ylim = ax.get_ylim()
    space = ylim[1] - ylim[0]
    size_adjust = gene_size / 20
    
    Point = namedtuple('Point', ['x', 'y'])
    p0 = Point(start, y)
    p1 = Point(start - size_adjust, y - (bezier_offset * space) -  adjust)
    p2 = Point(stop + size_adjust, y - (bezier_offset * space) - adjust)
    p3 = Point(stop, y)

    if not plot:
        return p0,p1,p2,p3

    verts = [
        p0,
        p1,
        p2,
        p3,        
        ]

    codes = [
            Path.MOVETO,
            Path.CURVE4,
            Path.CURVE4,
            Path.CURVE4,
            ]

    path = Path(verts, codes)
    patch = patches.PathPatch(path, facecolor='none', lw=.05, alpha=.7, ec='0') 
    ax.add_patch(patch)


def draw_canonical_splice(ax, start, stop, y, adjust, bezier_offset, plot=True):
    ''' Takes a start and a stop coordinate and generates a bezier curve underneath exons (for circle junctions).
        "bezier_offset" controls the depth of the circle junctions on the plot. '''

    xlim  = ax.get_xlim()
    xspace = xlim[1] -xlim[0]

    length = sqrt(abs((stop - start))/ xspace)    
    
    Point = namedtuple('Point', ['x', 'y'])
    p0 = Point(start, y)
    p1 = Point(start + .2 * (stop-start), y  + (length*bezier_offset) + adjust)
    p2 = Point(stop - .2 * (stop-start), y  + (length*bezier_offset) + adjust)
    p3 = Point(stop, y)
    if not plot:
        return p0,p1,p2,p3

    verts = [
        p0,
        p1,
        p2,
        p3,        
        ]

    codes = [
            Path.MOVETO,
            Path.CURVE4,
            Path.CURVE4,
            Path.CURVE4,
            ]

    path = Path(verts, codes)
    patch = patches.PathPatch(path, facecolor='none', lw=.05, alpha=.7, ec='0') 
    ax.add_patch(patch)


def plot_exons(ax, coordinates, y, height, strand, colors, numbering=False):
    '''Takes coordinates and coverage and plots exons in color with (lack of) alpha value representing relative coverage of an exon.'''

    exon_nums = list(range(1, len(coordinates) + 1))
    index = 0

    if strand == '-':
        exon_nums.reverse()

    for (start, stop), color in zip(coordinates, colors):
        length = stop - start
        rect = patches.Rectangle((start, y), length, height, facecolor=color, edgecolor='k', linewidth=.5)
        ax.add_patch(rect)

        if numbering:
            rx, ry = rect.get_xy()
            width = rect.get_width()
            height = rect.get_height()
            cx = rx + width / 2.0
            cy = ry + height / 2.0
            left, right = ax.get_xlim()

            # White numbers if exon is dark, otherwise black numbers.
            if width / (right - left) > .0065:
                if color[-1] >= 0.5:
                    num_color = 'w'
                else:
                    num_color = 'k'
                ax.annotate(str(exon_nums[index]), (cx, cy), color=num_color,  
                    fontsize=6, ha='center', va='center')
            index += 1


def plot_circles(ax, coordinates, y, gene_size, numbering=False, fig=None):
    '''Takes list of coordinate tuples (start, stop, counts) and plots backsplice curves using draw_backsplice()'''
    
    texts, boxes = [], []
    for start, stop, counts in coordinates:
        if counts != 0:
            step = 1.0 /counts
            factor = .25
            for num in arange(0.0, factor, factor * step):
                draw_backsplice(ax=ax, start=start, stop=stop, y=y, adjust=num, bezier_offset=.1, gene_size=gene_size)

            if numbering:
                p0, p1, p2, p3 = draw_backsplice(ax=ax, start=start, stop=stop, y=y, adjust=num, bezier_offset=.1, gene_size=gene_size, plot=False)
                x_mid, y_mid = calc_bez_max(p0, p1, p2, p3)
                text = plt.annotate(str(counts), (x_mid, y_mid), ha='center', va='top', alpha=.2, fontsize=8, xytext=(x_mid, y_mid-.3), arrowprops={'arrowstyle':'-','alpha':.05, 'lw':1})
                texts.append(text)
                plt.draw()
                r = fig.canvas.get_renderer()
    
    if numbering:
        for text in texts:
            extent = text.get_window_extent(r).transformed(ax.transData.inverted())
            box = Box(extent.xmin, extent.xmax, extent.ymin, extent.ymax)
            boxes.append(box)

        for indexa, boxa in enumerate(boxes):
            for indexb, boxb in enumerate(boxes):
                if indexa != indexb:
                    new_boxb = intersect(boxa, boxb, subtract=True)
                    boxes[indexb] = new_boxb

        for text, box in zip(texts, boxes):
            x_mid = (box.x0 + box.x1)/2
            text.set_position((x_mid, box.y1)) 
    
        ymin = ax.get_ylim()[0]
        plt.ylim([min([ymin, min(i.y0 for i in boxes)]), ax.get_ylim()[1]])


def plot_SJ_curves(ax, coordinates, y, numbering=False, fig=None):
    '''Takes list of coordinate tuples (start, stop, counts) and plots backsplice curves using draw_backsplice()'''

    texts, boxes = [], []
    for start, stop, counts in coordinates:
        if counts != 0:
            step = 1.0 /(counts)
            for num in arange(0.0, 0.1, 0.1 * step):
                draw_canonical_splice(ax=ax, start=start, stop=stop, y=y, adjust=num, bezier_offset=1)

            if numbering:
                p0, p1, p2, p3 = draw_canonical_splice(ax=ax, start=start, stop=stop, y=y, adjust=num, bezier_offset=1, plot=False)
                x_mid, y_mid = calc_bez_max(p0, p1, p2, p3)
                text = plt.annotate(str(counts), (x_mid, y_mid), ha='center', va='bottom', alpha=.2, fontsize=8, xytext=(x_mid, y_mid+.1), arrowprops={'arrowstyle':'-','alpha':.05, 'lw':1})
                texts.append(text)
                plt.draw()
                r = fig.canvas.get_renderer()
    
    if numbering:
        for text in texts:
            extent = text.get_window_extent(r).transformed(ax.transData.inverted())
            box = Box(extent.xmin, extent.xmax, extent.ymin, extent.ymax)
            boxes.append(box)

        for indexa, boxa in enumerate(boxes):
            for indexb, boxb in enumerate(boxes):
                if indexa != indexb:
                    new_boxb = intersect(boxa, boxb, subtract=False)
                # print(new_boxb.y0)
                    boxes[indexb] = new_boxb

        for text, box in zip(texts, boxes):
            x_mid = (box.x0 + box.x1)/2
            text.set_position((x_mid, box.y0))

        ymax = ax.get_ylim()[1]
        plt.ylim([ax.get_ylim()[0], max([ymax, max(i.y1 for i in boxes)])])


def scale_introns(coords, scaling_factor):
    '''Reduces intron size, returns new exon coordinates'''

    if scaling_factor <= 0:
        print("Intron scaling factor must be > 0. Plotting without scaling.")
        return coords

    newcoords = []
    newcoords.append(coords[0])
    
    for i in range(1, len(coords)):
        length = coords[i][1] - coords[i][0] 
        exonEnd = coords[i-1][1] 
        nextExonStart = coords[i][0] 
        intron = (nextExonStart - exonEnd) / scaling_factor 
        left = newcoords[i-1][1] + intron
        right = left + length
        newcoords.append((left, right)) 

    return newcoords


def transform(original, scaled, query):
    ''' Transform query to new scale. 
        Adapted from https://stackoverflow.com/questions/929103/convert-a-number-range-to-another-range-maintaining-ratio'''

    orig_c = [i for j in original for i in j]
    scal_c = [i for j in scaled for i in j]

    for i in range(len(orig_c) - 1):
        left, right = orig_c[i:i+2]
        if left <= query <= right:
            break

    if len(scal_c) > i + 2:
        n_left, n_right = scal_c[i:i+2]
    else:
        n_left = scal_c[i]
        n_right = query

    n_range = n_right - n_left
    o_range = right - left 
    if o_range == 0:
        return n_left

    return (((query - left) * n_range) / o_range) + n_left


def scale_coords(oldranges, newranges, coords):
    '''Scale junction coordinates to new exon coordinates using scale()'''

    newcoords = []
    for start, stop, counts in coords:
        newstart = transform(oldranges, newranges, start)
        newstop = transform(oldranges, newranges, stop)
        newcoords.append((newstart, newstop, counts))
    
    return newcoords


def to_rgb(color):
    '''Converts hex or color name to rgb. Coverage is set up to be represented by 'alpha' of rgba'''
    
    colordict = {
        'red': '#FF0000',
        'blue': '#0000FF',
        'green': '#006600',
        'yellow': '#FFFF00',
        'purple': '#990099',
        'black': '#000000',
        'white': '#FFFFFF',
        'orange': '#FF8000',
        'brown': '#663300'
    }

    if type(color) != str:
        print("Invalid color input: %s\n Color is set to red" % color)
        return (1,0,0)
         
    if color[0] != '#' or len(color) != 7:
        if color in colordict:
            color = colordict[color.lower()]
        else:
            print("Invalid color input: %s\n Color is set to red" % color)
            return (1,0,0)
    try: 
        rgb = tuple([int(color[i:i+2], 16)/255.0 for i in range(1, len(color), 2)])

    except ValueError:
        print("Invalid hex input: %s. Values must range from 0-9 and A-F.\n Color is set to red" % color)
        return (1,0,0)

    return rgb


def add_ax(num_plots, n, coordinates, strand, numbering, samples, sample_ind):
    '''Add new plot'''

    name, canonical,  circle,_, colors = samples[sample_ind]

    # Center the plot on the canvas
    ax = plt.subplot(num_plots, 1, n)
    ybottom = height = 0.5
    ytop = ybottom + height

    # Calculated again here in case user requests intron scaling.
    transcript_start = min([int(i[0]) for i in coordinates]) 
    transcript_stop = max([int(i[1]) for i in coordinates])  
    gene_length = transcript_stop - transcript_start
    
    # Add room on left and right of plot.
    x_adjustment = 0.05 * gene_length

    # Add room on top and bottom of plot. Include enough space here, otherwise curves will exceed the ax limits.
    y_adjustment = 4 * (ytop * height)

    xmin = transcript_start - x_adjustment
    xmax = transcript_stop + x_adjustment
    ymin = ybottom - y_adjustment
    ymax = ytop + y_adjustment
    ax.set_xlim([xmin, xmax])
    ax.set_ylim([ymin, ymax])

    # Turn off axis labeling.
    ax.axes.get_yaxis().set_visible(False)
    ax.axes.get_xaxis().set_visible(False)
    
    # Plot.
    
    plot_exons(ax=ax, coordinates=coordinates, colors=colors, height=height, y=ybottom, strand=strand, numbering=args.exon_numbering)
    plot_SJ_curves(ax=ax, coordinates=canonical, y=ytop)
    plot_circles(ax=ax, coordinates=circle, y=ybottom, gene_size=gene_length)

    # Replace special characters with spaces and plot sample name above each subplot.
    name = re.sub(r'[-_|]',' ', name)
    ax.set_title(name)


def exons(path, gene, transcript=False):
    '''Given a gene or transcript, returns exon coordinates from gtf file -> (chromosome, 5prime coord, 3prime coord, strand) for each exon
        If transcript is False, searches gtf file for gene name , determines the longest daughter transcript, returns exon coordinates. 
        Otherwise, directly returns transcript specific exon coordinates. '''

    transcript_dict = defaultdict(list)

    # Search by transcript ID or gene name.
    if transcript:
        prog = re.compile('transcript_id "%s"' % gene)
    else:
        prog = re.compile('gene_name "%s"' % gene)

    Exon = namedtuple('Exon', ['chromosome', 'source', 'feature', 'start', 'stop', 'score', 'strand', 'frame'])
    with open(path) as gtf:
        for line in gtf:

            # Skip header lines
            if line[0] == '#':
                continue

            # Only lines with gene name (ignore case)
            if prog.search(line, re.IGNORECASE):
                *line, attributes = line.split('\t')
                exon = Exon(*line)
                if exon.feature != 'exon':
                    continue
                
                transcript = attributes.split('transcript_id ')[1].split('"')[1]
                transcript_dict[transcript].append((exon.chromosome, int(exon.start), int(exon.stop), exon.strand))

    if len(transcript_dict) == 0:
        sys.exit("Gene {} not found in gtf file.".format(gene))
    
    elif len(transcript_dict) != 1:
        
        # Quantify transcript isoform lengths.
        lengths = defaultdict(int)
        for transcript, exons in transcript_dict.items():
            for (_,start, stop,_) in exons:
                lengths[transcript] += (stop - start) + 1
        
        # Determine longest transcript isoform.
        longest_length = 0
        for transcript, length in lengths.items():
            if length >= longest_length:
                longest_length = length
                longest_transcript = transcript
    else:
        longest_transcript = transcript

    # Exon coordinates of longest transcript.
    exon_info = transcript_dict[longest_transcript]

    coordinates = [(start, stop) for _, start, stop,_ in exon_info]
    coordinates.sort(key = lambda x:x[0])
    chromosome = set([i[0] for i in exon_info])
    if len(chromosome) == 1:
        chromosome = chromosome.pop()
    else:
        sys.exit("Gene {} found on more than one chromosome. Please fix GTF file.".format(gene))

    strand = set([i[3] for i in exon_info])
    
    if len(strand) == 1:
        strand = strand.pop()
    else:
        sys.exit("Gene {} found on both DNA strands. Please fix GTF file.".format(gene))
    
    return chromosome, coordinates, strand


def prep_bam(path):

    sam = pysam.AlignmentFile(path)
    try:
        sam.check_index()
    except ValueError:
        print("\nNo index found for %s..indexing\n" % path)
        try:
            pysam.index(path)
        except:
            print("\nBAM needs to be sorted first..sorting\n")
            pysam.sort("-o", path.replace('bam', 'sorted.bam'), path)
            print("\nIndexing..\n")
            pysam.index(path)
            print("Done")
    except AttributeError:
        print("\nSAM needs to be converted to BAM..converting\n")
        pysam.view('-bho', path.replace('sam', 'bam'), path)
        sam = pysam.AlignmentFile(path.replace('sam', 'bam'))

        try:
               
            sam.check_index()
        except ValueError:
            try:
                print("\nindexing..\n") 
                pysam.index(path)
            except:
                print("\nSorting before index..\n")
                pysam.sort("-o", path.replace('bam', 'sorted.bam'), path)
                print("\nindexing..\n") 
                pysam.index(path)
                print("Done.")
    return sam


def get_coverage(bam, chromosome, start, stop, strand=None, rev=False, average=True):
    '''Takes AlignmentFile instance and returns coverage and bases in a dict with position as key'''
    
    try:
        pileup = bam.pileup(chromosome, int(start), int(stop))

    except ValueError:
        if 'chr' not in chromosome:
            pileup = bam.pileup('chr' + chromosome, int(start), int(stop))
        else:
            pileup = bam.pileup(chromosome.replace('chr',''), int(start), int(stop))

    coverage = defaultdict(list)
    
    strandswitch = {'+': '-', '-': '+'}

    if strand and strand in strandswitch:
        if rev:
            strand = strandswitch[strand]
        
        for column in pileup:
            for read in column.pileups:    
                if read.alignment.is_read1 and ((strand == '+' and read.alignment.is_reverse) or (strand == '-' and not read.alignment.is_reverse)):       
                    continue
                if read.alignment.is_read2 and ((strand == '+' and not read.alignment.is_reverse) or (strand == '-' and read.alignment.is_reverse)):       
                    continue
                if not read.is_del and not read.is_refskip:
                    base = read.alignment.query_sequence[read.query_position]
                    coverage[column.pos].append(base)
    
    # If sequencing was not strand-specific
    else:
        for column in pileup:
            for read in column.pileups:  
                if not read.is_del and not read.is_refskip:
                    base = read.alignment.query_sequence[read.query_position]
                    coverage[column.pos].append(base)

    for key in coverage:
        c = Counter(coverage[key])
        sum_c = sum(c.values())
        coverage[key] = (sum_c, c)

    if not average:
        return coverage

    avg = []
    for i in coverage:
        avg.append(coverage[i][0])

    if len(avg)>0:
        return sum(avg) / (stop - start + 1)
    else:
        return 0


def fetch(bam, chromosome, upstream, downstream):
    
    try:
        fetched = bam.fetch(chromosome, upstream, downstream)
    except ValueError:   
        if 'chr' not in chromosome:
            fetched = bam.fetch('chr' + chromosome, upstream, downstream)
        else:
            try:
                fetched = bam.fetch(chromosome.replace('chr', ''), upstream, downstream)
            except ValueError:
                sys.exit("Chromosome %s not found in bam file.." % chromosome)
    return fetched

def strand_filter(read, strand=None, rev=False):

    if not strand:
        return 1
    
    strand_switch = {'+': '-', '-': '+'}
    if strand not in strand_switch:
        return 1

    if rev:
        strand = strand_switch[strand]

    if read.is_read1 and ((strand == '+' and read.is_reverse) or (strand == '-' and not read.is_reverse)):       
        return 0
    if read.is_read2 and ((strand == '+' and not read.is_reverse) or (strand == '-' and read.is_reverse)):       
        return 0
    
    return 1
    

def junctions(bam, chromosome, upstream, downstream, min_junctions, strand=None, rev=False):

    fetched = fetch(bam, chromosome, upstream, downstream)

    stranded = (read for read in fetched if strand_filter(read, strand, rev))
 
    introns = bam.find_introns(stranded).items()
    
    filtered_junctions = []
    for (start, stop), count in introns:
        if start >= upstream and stop <= downstream and count >= min_junctions:
            filtered_junctions.append((start, stop, count))
    
    return filtered_junctions


def circles(bam, chromosome, upstream, downstream, min_overhang, min_junctions, strand=None, rev=False):

    circ_d = defaultdict(int)

    fetched = fetch(bam, chromosome, upstream, downstream)

    for read in fetched:
        if strand_filter(read, strand, rev) and read.has_tag('SA') and not read.is_supplementary:
            
            supp_chromosome, supp_start, supp_strand, supp_cigar, *_  = read.get_tag('SA').split(',')
            
            # Interested only in circles, not fusions
            if supp_chromosome != read.reference_name:
                continue
            
            start = read.reference_start
            cigar = read.cigarstring
                       
            r1 = sum(list(map(int,re.findall('([0-9]+)M', cigar)))) 
            r2 = sum(list(map(int,re.findall('([0-9]+)M', supp_cigar))))
  
            if not (r1 > min_overhang and r2 > min_overhang):
                continue

            donor = min([read.reference_end + 1, int(supp_start)])
            acceptor = max([read.reference_end + 1, int(supp_start )]) 
            circ_d[(donor, acceptor)] += 1

    filtered_junctions = []
    for (start, stop), count in circ_d.items():
        if start >= upstream and stop <= downstream and count >= min_junctions:
            filtered_junctions.append((start, stop, count))
    
    return filtered_junctions


def main():

    args = parse()
    
    if args.gene:
        chromosome, exon_coordinates, strand  = exons(path=args.gtf, gene=args.gene)
    else:
        chromosome, exon_coordinates, strand = exons(path=args.gtf, gene=args.transcript, transcript=True)

    transcript_start = min(i[0] for i in exon_coordinates)
    transcript_stop = max(i[1] for i in exon_coordinates)

    min_junctions = args.filter


    if args.intron_scale:
        factor = args.intron_scale
        scaled_coords = scale_introns(exon_coordinates, factor)


    if args.stranded:
        if args.stranded == 'reverse':
            rev = True
            
        else:
            rev = False
        new_strand = strand
    else:
        rev = False
        new_strand = None

    samples = []
    
    for bampath in args.bam:
        name = os.path.basename(bampath).split('.')[0].upper()
        bam = prep_bam(bampath)
        canonical = junctions(bam, chromosome, transcript_start, transcript_stop, min_junctions, strand=new_strand, rev=rev)
        circle = circles(bam, chromosome, transcript_start, transcript_stop, min_overhang=10, min_junctions=2, strand=new_strand, rev=rev)
        
        coverage=[]
        for start, stop in exon_coordinates:
            coverage.append(get_coverage(bam, chromosome, start, stop, strand=new_strand, rev=rev, average=True))
            
        if args.intron_scale:
 
            canonical = scale_coords(exon_coordinates, scaled_coords, canonical)
            circle = scale_coords(exon_coordinates, scaled_coords, circle)


        if args.reduce_canonical:
            # Avoid division by 0 or negative number.
            if args.reduce_canonical <= 0:
                print("-rc/ --reduce_canonical must be > 0. Not reducing canonical junctions.")
            else:
                canonical = [(i, j, k // args.reduce_canonical) for i, j, k in canonical]

        if args.reduce_backsplice:
            if args.reduce_backsplice <= 0:
                print("-rbs/ --reduce_backsplice must be > 0. Not reducing backsplice junctions.")
            else:
                circle = [(i, j, k // args.reduce_backsplice) for i, j, k in circle]

        samples.append((name, canonical, circle, coverage))
    if args.intron_scale:
        exon_coordinates = scaled_coords

    if args.normalize:
        highest = 0

        for index in range(len(samples)): 

            coverage = samples[index][3]
            max_coverage = max(coverage)
            if max_coverage > highest:
                highest = max_coverage

    for index in range(len(samples)):
        coverage = samples[index][3]

        if args.normalize:
            max_coverage = highest
        else:
            max_coverage = max(coverage)
        if max_coverage != 0:
            color = [args.color + (i / max_coverage, ) for i in coverage]
        else:
            color = [args.color + (0, ) for i in coverage] 
        
        samples[index] += (color, )

    # Plot for each sample
    num_plots = len(args.bam)
    fig = plt.figure(figsize=(15, 4 * num_plots))
    
    for i in range(len(samples)):
        name, canonical, circle, _, colors = samples[i]

        # Center the plot on the canvas
        ax = plt.subplot(num_plots, 1, i+1)
        ybottom = height = 0.5
        ytop = ybottom + height

        # Calculated again here in case user requests intron scaling.
        transcript_start = min([int(i[0]) for i in exon_coordinates]) 
        transcript_stop = max([int(i[1]) for i in exon_coordinates])  
        gene_length = transcript_stop - transcript_start

        # Add room on left and right of plot.
        x_adjustment = 0.05 * gene_length

        # Add room on top and bottom of plot. Include enough space here, otherwise curves will exceed the ax limits.
        y_adjustment = 4 * (ytop * height)

        xmin = transcript_start - x_adjustment
        xmax = transcript_stop + x_adjustment
        ymin = ybottom - y_adjustment
        ymax = ytop + y_adjustment
        ax.set_xlim([xmin, xmax])
        ax.set_ylim([ymin, ymax])

        # Turn off axis labeling.
        ax.axes.get_yaxis().set_visible(False)
        ax.axes.get_xaxis().set_visible(False)

        # Plot.
        plot_exons(ax=ax, coordinates=exon_coordinates, colors=colors, height=height, y=ybottom, strand=strand, numbering=args.exon_numbering)
        plot_SJ_curves(ax=ax, coordinates=canonical, y=ytop)
        plot_circles(ax=ax, coordinates=circle, y=ybottom, gene_size=gene_length)

        # Replace special characters with spaces and plot sample name above each subplot.
        name = re.sub(r'[-_|]',' ', name)
        ax.set_title(name)
        

    #plt.subplots_adjust(hspace=0.4, top=0.85, bottom=0.1)
    if args.gene:
        title = args.gene
    else:
        title = args.transcript

    plt.tight_layout()
    plt.savefig("%s.svg" % title)
    html_str = '''
    <html>
    <body>
    <img src="%s.svg" alt="Cannot find %s.svg. Make sure the html file and svg file are in the same directory">
    </body>
    </html>
    '''

    with open("%s.html" % title, "w") as html:
        html.write(html_str % (title, title))

    if not args.repress_open:
        webbrowser.open('file://' + os.path.realpath("%s.html" % title))
main()
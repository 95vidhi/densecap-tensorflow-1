'''Train the model'''
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import datetime
import os
import json
import gc

import tensorflow as tf
import numpy as np
import scipy.misc

import densecap.model as model
import densecap.util as util


FLAGS = tf.app.flags.FLAGS
tf.app.flags.DEFINE_string('train_dir', os.getcwd(), 'Directory with training data (images)')
tf.app.flags.DEFINE_string('region_desc', '', 'Region descriptions file (Visual Genome format)')
tf.app.flags.DEFINE_string('log_dir', os.path.join(os.getcwd(), 'logs'), 'Directory with logs for tensorboard')
tf.app.flags.DEFINE_string('ckpt_dir', os.path.join(os.getcwd(), 'ckpt'), 'Directory for model checkpoints')
tf.app.flags.DEFINE_integer('batch_size', 64, 'Batch size')
tf.app.flags.DEFINE_integer('limit', 0, 'Limit training process to first `limit` files per epoch')
tf.app.flags.DEFINE_integer('epoch', 10, 'Epoch count')
tf.app.flags.DEFINE_integer('log_every', 100, 'Print log messages every `log_every` steps')
tf.app.flags.DEFINE_integer('save_every', 100, 'Save model checkpoint every `save_every` steps')
tf.app.flags.DEFINE_integer('eval_every', 100, 'Eval model every `eval_every` steps')
tf.app.flags.DEFINE_float('learning_rate', 0.01, 'Initial learning rate')


def train_data(filename, limit):
    with open(FLAGS.region_desc) as ifile:
        data = json.load(ifile)

    limit = limit or len(data)
    for idx in range(limit):
        record = data[idx]

        filename = os.path.join(
            FLAGS.train_dir, str(record['id']) + '.jpg')
        # XXX: resize to have 600/720 longer side
        image = scipy.misc.imread(filename, mode='RGB')
        height, width, _ = image.shape
        fraction = 720.0 / max(height, width)
        image = scipy.misc.imresize(image, fraction)
        gt_boxes = np.array([[r['y'], r['x'], r['height'], r['width']]
                             for r in record['regions']])
        yield (image, gt_boxes)


def load_vgg16_weights(sess):
    with np.load('data/vgg16_weights.npz') as ifile:
        for v in tf.global_variables():
            name = v.name.replace('weights', 'W').replace('biases', 'b').replace('/', '_')[:-2]
            if name in ifile:
                sess.run(tf.assign(v, ifile[name]))


def main(_):
    '''entry point'''

    image_input = tf.placeholder(tf.float32, shape=[1, None, None, 3])

    vgg16 = model.VGG16(image_input)
    rpn = model.RegionProposalNetwork(vgg16.layers['conv5_3'])

    current_run_log_dir = os.path.join(
        FLAGS.log_dir,
        datetime.datetime.now().isoformat()
    )
    writer = tf.train.SummaryWriter(current_run_log_dir, graph=tf.get_default_graph())
    saver = tf.train.Saver()
    saved_model = tf.train.latest_checkpoint(FLAGS.ckpt_dir)

    if not os.path.exists(FLAGS.ckpt_dir):
        os.makedirs(FLAGS.ckpt_dir)


    with tf.Session() as sess:
        if saved_model:
            saver.restore(sess, saved_model)
        else:
            print('Prevous model not found, starting from scratch.')
            sess.run(tf.global_variables_initializer())
            load_vgg16_weights(sess)

        for epoch in range(FLAGS.epoch):
            for image, gt_boxes in train_data(FLAGS.region_desc, FLAGS.limit):
                height, width, _ = image.shape
                merged = tf.summary.merge_all()

                loss, step, summary, _ = sess.run(
                    [rpn.loss, rpn.global_step, merged, rpn.train_op], {
                        vgg16.input: [image],
                        rpn.image_height: height,
                        rpn.image_width: width,
                        rpn.ground_truth: gt_boxes,
                        rpn.ground_truth_num: len(gt_boxes)
                    })

                writer.add_summary(summary, global_step=step)

                gc.collect()

                if not step % FLAGS.log_every:
                    print('\rEpoch {:<2} step {:<6} loss: {:<8.2f}'\
                        .format(epoch+1, step, loss), end='')

                if not step % FLAGS.save_every:
                    saver.save(
                        sess,
                        os.path.join(FLAGS.ckpt_dir, 'densecap'),
                        global_step=rpn.global_step)

                if not step % FLAGS.eval_every:
                    k = 50
                    boxes, scores = sess.run(
                        [rpn.proposals, tf.nn.softmax(rpn.scores)], {
                            rpn.image_height: height,
                            rpn.image_width: width,
                            vgg16.input: [image]
                        })
                    np_proposals = np.squeeze(boxes[np.argsort(scores[:, 1])][-k:])

                    proposals = tf.placeholder(tf.float32, [None, 4])
                    ground_truth = tf.placeholder(tf.float32, [None, 4])
                    proposals_num = tf.placeholder(tf.int32)
                    ground_truth_num = tf.placeholder(tf.int32)

                    recall = sess.run(model.recall(
                        proposals, proposals_num, ground_truth, ground_truth_num, 0.7), {
                            proposals: np_proposals,
                            proposals_num: k,
                            ground_truth: gt_boxes,
                            ground_truth_num: len(gt_boxes)
                        })

                    summary = tf.Summary(value=[
                        tf.Summary.Value(tag='recall', simple_value=float(recall)),
                    ])
                    writer.add_summary(summary, global_step=step)


        print()

    writer.close()


if __name__ == '__main__':
    tf.app.run()
